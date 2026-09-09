"""The landing page's image (DESIGN.md 11.5).

The front page is the one thing an unauthenticated visitor sees, so its image
has to be servable without a session. It is therefore **not** a photo from the
collection: it is a separate file, resized once into
``$DERIVED_ROOT/public/`` under fixed literal names.

That matters more than it looks. The obvious alternative — pointing the page at
a photo and making an exception in the image route — would put an ACL hole in
the one place that must not have one. Here, no part of the request ever reaches
a path: the route serves `landing-640` or `landing-1280` and nothing else.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageOps

from .config import Config

log = logging.getLogger("harelphotos.public")

WIDTHS = (640, 1280)
PUBLIC_DIR = "public"
STEM = "landing"


def public_dir(cfg: Config) -> Path:
    return cfg.derived_root / PUBLIC_DIR


def asset_path(cfg: Config, name: str) -> Path | None:
    """Resolve one of the fixed names. Anything else is None, never a path."""
    for width in WIDTHS:
        for ext in ("avif", "jpeg"):
            if name == f"{STEM}-{width}.{ext}":
                return public_dir(cfg) / name
    return None


def build(cfg: Config, force: bool = False) -> list[str]:
    """Derive the landing image, if one is configured. Returns what it wrote."""
    src = cfg.ui.landing_image
    if not src:
        return []
    if not src.is_file():
        log.warning("[ui] landing_image does not exist: %s", src)
        return []

    out = public_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            for width in WIDTHS:
                scale = min(1.0, width / im.width)
                size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
                small = im if scale == 1.0 else im.resize(size, Image.LANCZOS)
                for ext, fmt, params in (
                    ("avif", "AVIF", {"quality": 60, "speed": 6}),
                    # A JPEG alongside it, because this page has to work for
                    # whoever turns up, including a browser without AVIF.
                    ("jpeg", "JPEG", {"quality": 85, "optimize": True}),
                ):
                    dest = out / f"{STEM}-{width}.{ext}"
                    if dest.exists() and not force:
                        continue
                    tmp = dest.with_name(dest.name + ".tmp")
                    small.save(tmp, fmt, **params)
                    tmp.replace(dest)
                    written.append(dest.name)
    except Exception as e:
        log.warning("cannot prepare the landing image from %s: %s", src, e)
        return written
    return written


def hero(cfg: Config) -> dict | None:
    """What the template needs, or None if there is no image to show."""
    d = public_dir(cfg)
    have = [w for w in WIDTHS if (d / f"{STEM}-{w}.avif").is_file()]
    if not have:
        # Fall back to the JPEG, in case AVIF encoding was unavailable.
        have = [w for w in WIDTHS if (d / f"{STEM}-{w}.jpeg").is_file()]
        if not have:
            return None
        ext = "jpeg"
    else:
        ext = "avif"
    return {
        "src": f"/public/{STEM}-{max(have)}.{ext}",
        "srcset": ", ".join(f"/public/{STEM}-{w}.{ext} {w}w" for w in sorted(have)),
    }
