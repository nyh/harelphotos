"""Generating the derivative images (DESIGN.md 9.1).

One decode per photo, then a **descending cascade**: each tier is resized from
the tier above it rather than from the original. That is what makes the three
smaller tiers nearly free — measured at 400 ms for four tiers versus ~1.2 s if
each came from the full-size image.

Everything here runs at scan time, in a worker process. Nothing in this module
is ever called while serving a request.
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter, ImageOps

from .config import Config

# Bumped whenever a change in THIS FILE changes the images produced, so that
# an upgrade regenerates them. Distinct from config's `recipe_version`, which
# is the owner's own lever: this one is ours, and they should not have to know
# that the pipeline changed in order to benefit from it.
#
#   2  the smallest tier covering an over-sized original now holds the
#      original's own size, instead of that tier being skipped entirely
PIPELINE_VERSION = 2

# Sharpening after a downscale. Downscaled photos always look soft; this is
# what makes the grid crisp rather than mushy. Gentler on the small tiers,
# where over-sharpening reads as noise and costs bytes.
UNSHARP_LARGE = (0.6, 60, 3)
UNSHARP_SMALL = (0.5, 50, 3)
SMALL_TIER_PX = 512

# Pillow raises on absurdly large images by default; a decompression bomb is a
# real concern for files we did not create. This is generous for photographs
# (roughly a 500 MP image) while still refusing something pathological.
Image.MAX_IMAGE_PIXELS = 500_000_000

_SRGB = None


def _srgb_profile():
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.createProfile("sRGB")
    return _SRGB


def to_srgb(im: Image.Image) -> Image.Image:
    """Convert to sRGB if the file carries a different ICC profile.

    Without this, photos from a camera set to AdobeRGB come out visibly flat,
    because browsers assume sRGB for an image with no profile and we strip
    profiles from derivatives.
    """
    icc = im.info.get("icc_profile")
    if not icc:
        return im
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.profileToProfile(im, src, _srgb_profile(), outputMode="RGB")
    except Exception:
        # A malformed profile is not a reason to lose the photo.
        return im


def fit(im: Image.Image, target: int) -> Image.Image:
    """Scale so the longest side is `target`. Never upscales."""
    w, h = im.size
    scale = min(1.0, target / max(w, h))
    if scale == 1.0:
        return im
    return im.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))),
        Image.LANCZOS,
        reducing_gap=2.0,
    )


def dominant_colour(im: Image.Image) -> str:
    """A single colour to sit behind a lazy-loading thumbnail."""
    try:
        r, g, b = im.convert("RGB").resize((1, 1), Image.LANCZOS).getpixel((0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#888888"


def deriv_key(content_sig: bytes | None, cfg: Config) -> str:
    """Fingerprint of everything that would change the output.

    Deliberately built from `content_sig` and never from mtime: timestamps get
    rewritten without pixels changing, and making derivatives depend on them
    would turn a metadata tidy-up into a full re-encode (DESIGN.md 7).
    """
    e = cfg.encode
    recipe = "|".join(
        [
            e.format,
            str(PIPELINE_VERSION),
            str(e.recipe_version),
            str(e.speed),
            e.subsampling,
            ",".join(f"{t}:{e.quality_for(t)}" for t in cfg.sizes.tiers),
        ]
    )
    h = hashlib.blake2b(digest_size=12)
    h.update(content_sig or b"")
    h.update(recipe.encode())
    return h.hexdigest()


def derived_path(cfg: Config, tier: int, relpath: str, fmt: str | None = None) -> Path:
    """`<derived>/<tier>/<relative path>.<ext>`

    The original extension is kept before the new one, so `a.jpg` and `a.png`
    in one directory stay distinct.
    """
    ext = (fmt or cfg.encode.format).lower()
    return cfg.derived_root / str(tier) / f"{relpath}.{ext}"


def save_atomic(im: Image.Image, path: Path, cfg: Config, tier: int) -> int:
    """Encode to a temporary file and rename into place.

    An interrupted scan must never leave a truncated image behind; the rename
    is what makes `^C` and re-run safe.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    e = cfg.encode
    params: dict = {"quality": e.quality_for(tier)}
    if e.format == "avif":
        params.update(speed=e.speed, subsampling=e.subsampling)
    elif e.format == "webp":
        params.update(method=4)
    else:
        params.update(optimize=True, progressive=True)
    try:
        im.save(tmp, e.format.upper(), **params)
        size = tmp.stat().st_size
        os.replace(tmp, path)
        return size
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


@dataclass
class DeriveResult:
    tiers: list[int] = field(default_factory=list)
    colour: str | None = None
    bytes_written: int = 0
    error: str | None = None


def derive(path: Path, relpath: str, cfg: Config) -> DeriveResult:
    """Produce every configured tier for one photo. Never raises."""
    out = DeriveResult()
    tiers = list(cfg.sizes.tiers)          # descending
    if not tiers:
        return out
    try:
        im = Image.open(path)
        # libjpeg can decode at 1/2, 1/4 or 1/8 scale. Measured to cut decode
        # from ~450 ms to ~93 ms, and it is the single biggest win here.
        im.draft("RGB", (tiers[0], tiers[0]))
        im = ImageOps.exif_transpose(im)
        im = to_srgb(im)
        im = im.convert("RGB")
    except Exception as e:
        out.error = f"cannot decode: {e}"
        return out

    longest = max(im.size)
    # Never upscale — but do not throw away resolution either. A 1174 px photo
    # with tiers 2048/1280/512 must not end up with 512 px as its largest
    # view; the smallest tier that covers it is generated at the original's
    # own size instead. Bigger tiers than that are skipped.
    covering = [t for t in tiers if t >= longest]
    native_tier = min(covering) if covering else None

    cur = im
    try:
        for px in tiers:
            if px > longest and px != native_tier:
                continue
            cur = fit(cur, px)      # a no-op at the native tier
            radius, percent, threshold = UNSHARP_LARGE if px > SMALL_TIER_PX else UNSHARP_SMALL
            # Sharpening compensates for downscaling; at native size there was
            # no downscale, so it would just add noise and bytes.
            shaped = (
                cur if px == native_tier
                else cur.filter(ImageFilter.UnsharpMask(radius, percent, threshold))
            )
            dest = derived_path(cfg, px, relpath)
            written = save_atomic(shaped, dest, cfg, px)
            # Re-encoding a small photo can produce something bigger than the
            # original. Serving that would be worse than serving the original.
            if px == native_tier and written >= path.stat().st_size:
                dest.unlink(missing_ok=True)
            else:
                out.bytes_written += written
                out.tiers.append(px)
            if px == tiers[-1]:
                out.colour = dominant_colour(shaped)
    except Exception as e:
        out.error = f"cannot encode: {e}"
        return out

    if out.colour is None:
        out.colour = dominant_colour(cur)
    return out


def remove_derivatives(cfg: Config, relpath: str) -> int:
    """Delete every derivative of one photo, in all tiers and all formats."""
    removed = 0
    for tier_dir in _tier_dirs(cfg):
        for fmt in ("avif", "webp", "jpeg", "jpg"):
            p = tier_dir / f"{relpath}.{fmt}"
            try:
                p.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return removed


def _tier_dirs(cfg: Config) -> list[Path]:
    """Every tier directory present on disk, including stale ones and the
    on-demand fallback trees."""
    roots: list[Path] = []
    if not cfg.derived_root.is_dir():
        return roots
    for entry in cfg.derived_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.isdigit():
            roots.append(entry)
        elif entry.name in ("webp", "jpeg"):
            roots.extend(p for p in entry.iterdir() if p.is_dir() and p.name.isdigit())
    return roots


def prune_empty_dirs(root: Path) -> int:
    """Remove directories left empty after deleting derivatives."""
    removed = 0
    if not root.is_dir():
        return 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == str(root):
            continue
        if not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
                removed += 1
            except OSError:
                pass
    return removed
