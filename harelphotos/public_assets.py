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

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging
import os
from pathlib import Path

from PIL import Image, ImageOps

from .config import Config

log = logging.getLogger("harelphotos.public")

# The default display width, and twice it for high-density screens. Both are
# derived from `[ui] hero_width`; this pair is only the fallback.
WIDTHS = (640, 1280)
PUBLIC_DIR = "public"
STEM = "landing"

# The album's icon, in the sizes the places that use it ask for: 192 and 512
# for Android, 180 for iOS's apple-touch-icon, 32 for a desktop browser's tab
# and the emblem beside the front page's heading. PNG because that is what
# every platform accepts for an installed app -- this is the one place AVIF is
# not the answer.
ICON_STEM = "icon"
ICON_SIZES = (32, 180, 192, 512)

# iOS composites an apple-touch-icon onto black instead of honouring its alpha
# channel, so a logo with transparent corners becomes a black tile with a
# picture in the middle. That one size is flattened onto white; every other
# keeps its transparency, which is what lets the favicon sit on a light or a
# dark tab strip and the heading emblem on either colour scheme.
OPAQUE_ICON_SIZES = frozenset({180})
OPAQUE_ICON_BACKGROUND = (255, 255, 255)

# The smallest icon worth listing in the web app manifest. Chrome wants at
# least 192 before it will offer to install a site at all, so anything below
# that is only there to be picked by mistake.
MANIFEST_MIN_ICON = 180

# What the icons on disk were made from. Without it `build_icons` skips every
# size that already exists, so changing the configured icon would edit the
# config, restart the server, and change nothing at all.
ICON_STAMP = "icon.source"


def public_dir(cfg: Config) -> Path:
    return cfg.derived_root / PUBLIC_DIR


def widths(cfg: Config) -> tuple[int, int]:
    """The display width and its 2x companion."""
    w = max(64, int(cfg.ui.hero_width or 640))
    return (w, w * 2)


def asset_path(cfg: Config, name: str) -> Path | None:
    """Resolve one of the fixed names. Anything else is None, never a path."""
    for width in widths(cfg):
        for ext in ("avif", "jpeg"):
            if name == f"{STEM}-{width}.{ext}":
                return public_dir(cfg) / name
    for size in ICON_SIZES:
        if name == f"{ICON_STEM}-{size}.png":
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
            for width in widths(cfg):
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
                    # Unique per process: gunicorn starts several workers at
                    # once and they would otherwise share one temp file.
                    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
                    small.save(tmp, fmt, **params)
                    tmp.replace(dest)
                    written.append(dest.name)
    except Exception as e:
        log.warning("cannot prepare the landing image from %s: %s", src, e)
        return written
    return written


def icon_source(cfg: Config) -> Path | None:
    """The file the album's icon is made from.

    `[ui] icon` is the name to use; `app_icon` is what it was called when it
    only fed the installed app, and still works. Falling back to the landing
    image means a site that configured a hero and nothing else still gets an
    icon, which is better than a blank browser tab.
    """
    return cfg.ui.icon or cfg.ui.app_icon or cfg.ui.landing_image


def _icon_stamp(src: Path) -> str:
    st = src.stat()
    sizes = ",".join(str(s) for s in ICON_SIZES)
    return f"{src.resolve()}\n{st.st_mtime_ns}\n{st.st_size}\n{sizes}\n"


def _resize_square(im: Image.Image, size: int) -> Image.Image:
    """Scale a square RGBA image.

    LANCZOS both up and down. A 48px flat-colour logo blown up to 512 is soft,
    but it is smooth, and the alternative -- NEAREST -- keeps the edges hard
    and turns every curve into a staircase, which looks worse at every size
    anyone actually sees. Android draws the 192 on a launcher; the 512 is for
    a splash screen.

    Resizing RGBA directly, and deliberately. A transparent pixel is
    (0, 0, 0, 0) -- black -- and resampling averages that black into its
    neighbours, so the textbook advice is to bleed colour outwards first, or
    to premultiply. Measured on this icon, the raw colour channel does darken
    at the edge, from (231, 76, 60) to (187, 54, 41). It makes no visible
    difference: composited over white, the two methods differ by 2 units of
    luminance out of 255, because the pixels whose colour was darkened are
    exactly the ones with almost no alpha. Fifty lines of per-pixel Python
    were written for this and thrown away; do not put them back without a
    picture that looks wrong.

    The one thing that really did matter is keeping the alpha channel at all.
    This used to `convert("RGB")` first, which discards it and leaves the
    stored colour behind -- and the stored colour under a transparent corner
    is black, so a logo with rounded corners became an opaque black tile with
    a picture in the middle.
    """
    return im.resize((size, size), Image.LANCZOS)


def build_icons(cfg: Config, force: bool = False) -> list[str]:
    """The album's icon in every size that gets asked for.

    Centre-cropped rather than letterboxed if the source is not square: an
    icon is displayed as a square whatever we do, and padding it just makes the
    picture smaller. With no source configured there is no icon at all, the
    manifest omits them and the pages omit the links, which browsers accept.
    """
    src = icon_source(cfg)
    if not src:
        return []
    if not src.is_file():
        log.warning("[ui] icon does not exist: %s", src)
        return []
    out = public_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    # Rebuild everything when the source changes, so editing the config is
    # enough. The stamp covers the size list too: adding a size to ICON_SIZES
    # has to reach a machine whose icons were built before it existed.
    stamp = out / ICON_STAMP
    try:
        want = _icon_stamp(src)
        if stamp.is_file() and stamp.read_text() == want:
            pass
        else:
            force = True
    except OSError:
        force = True
        want = None

    try:
        with Image.open(src) as opened:
            im = ImageOps.exif_transpose(opened).convert("RGBA")
            if im.width != im.height:
                side = min(im.width, im.height)
                im = ImageOps.fit(im, (side, side), Image.LANCZOS,
                                  centering=(0.5, 0.4))
            for size in ICON_SIZES:
                dest = out / f"{ICON_STEM}-{size}.png"
                if dest.exists() and not force:
                    continue
                square = _resize_square(im, size)
                if size in OPAQUE_ICON_SIZES:
                    flat = Image.new("RGB", square.size, OPAQUE_ICON_BACKGROUND)
                    flat.paste(square, mask=square.split()[3])
                    square = flat
                tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
                square.save(tmp, "PNG", optimize=True)
                tmp.replace(dest)
                written.append(dest.name)
    except Exception as e:
        log.warning("cannot prepare the icons from %s: %s", src, e)
        return written

    if written and want:
        try:
            tmp = stamp.with_name(f"{stamp.name}.{os.getpid()}.tmp")
            tmp.write_text(want)
            tmp.replace(stamp)
        except OSError as e:                     # a rebuild every start, no worse
            log.warning("cannot record the icon source: %s", e)
    return written


def icons(cfg: Config) -> list[int]:
    """Which icon sizes actually exist on disk."""
    d = public_dir(cfg)
    return [s for s in ICON_SIZES if (d / f"{ICON_STEM}-{s}.png").is_file()]


def manifest(cfg: Config) -> dict:
    """The web app manifest.

    `display: standalone` is the whole point: opened from the home screen the
    site gets the URL bar's height back, which on a phone held sideways is a
    sixth of the screen.
    """
    title = cfg.ui.site_title or "Photos"
    data = {
        "name": title,
        "short_name": title[:12],
        "description": cfg.ui.tagline,
        # "/" rather than "/a/": it sends you to the albums when you are signed
        # in and to the login page when you are not, which is what launching
        # the icon should do in both cases.
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
    }
    # The favicon size is deliberately not offered here. A manifest is a list
    # of icons for installing the site, and a browser told about a 32px one is
    # entitled to put it on a home screen.
    have = [s for s in icons(cfg) if s >= MANIFEST_MIN_ICON]
    if have:
        # Omitted entirely when there is none: an empty list is a manifest
        # error, whereas an absent key just means the browser picks something.
        data["icons"] = [
            {
                "src": f"/public/{ICON_STEM}-{s}.png",
                "sizes": f"{s}x{s}",
                "type": "image/png",
                "purpose": "any",
            }
            for s in have
        ]
    return data


def hero(cfg: Config) -> dict | None:
    """What the template needs, or None if there is no image to show."""
    d = public_dir(cfg)
    ws = widths(cfg)
    have = [w for w in ws if (d / f"{STEM}-{w}.avif").is_file()]
    if not have:
        # Fall back to the JPEG, in case AVIF encoding was unavailable.
        have = [w for w in ws if (d / f"{STEM}-{w}.jpeg").is_file()]
        if not have:
            return None
        ext = "jpeg"
    else:
        ext = "avif"
    return {
        "src": f"/public/{STEM}-{max(have)}.{ext}",
        "srcset": ", ".join(f"/public/{STEM}-{w}.{ext} {w}w" for w in sorted(have)),
        # The width it is drawn at, so the page does not have to guess.
        "width": ws[0],
    }
