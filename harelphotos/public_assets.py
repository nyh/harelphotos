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
import os
from pathlib import Path

from PIL import Image, ImageOps

from .config import Config

log = logging.getLogger("harelphotos.public")

WIDTHS = (640, 1280)
PUBLIC_DIR = "public"
STEM = "landing"

# Home-screen icons. 192 and 512 are what Android asks for; 180 is what iOS
# uses for apple-touch-icon. PNG because that is what every platform accepts
# for an installed app -- this is the one place AVIF is not the answer.
ICON_STEM = "icon"
ICON_SIZES = (180, 192, 512)


def public_dir(cfg: Config) -> Path:
    return cfg.derived_root / PUBLIC_DIR


def asset_path(cfg: Config, name: str) -> Path | None:
    """Resolve one of the fixed names. Anything else is None, never a path."""
    for width in WIDTHS:
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


def build_icons(cfg: Config, force: bool = False) -> list[str]:
    """Square home-screen icons, cut from the landing image.

    Centre-cropped rather than letterboxed: an icon is displayed as a square
    whatever we do, and padding it just makes the picture smaller. Without a
    landing image there is no icon at all and the manifest omits them, which
    browsers accept -- they fall back to a screenshot of the page.
    """
    src = cfg.ui.app_icon or cfg.ui.landing_image
    if not src:
        return []
    if not src.is_file():
        log.warning("[ui] icon source does not exist: %s", src)
        return []
    out = public_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            for size in ICON_SIZES:
                dest = out / f"{ICON_STEM}-{size}.png"
                if dest.exists() and not force:
                    continue
                square = ImageOps.fit(im, (size, size), Image.LANCZOS, centering=(0.5, 0.4))
                tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
                square.save(tmp, "PNG", optimize=True)
                tmp.replace(dest)
                written.append(dest.name)
    except Exception as e:
        log.warning("cannot prepare the home-screen icons from %s: %s", src, e)
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
    have = icons(cfg)
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
