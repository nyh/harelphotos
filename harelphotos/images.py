"""Serving image bytes (DESIGN.md 9.4, 10.3, 10.4).

Two things happen here that are easy to get subtly wrong:

* **Content negotiation.** One URL returns AVIF, WebP or JPEG depending on what
  the browser said it accepts. That makes ``Vary: Accept`` mandatory — without
  it a cache can hand a client a format it cannot read, and with a year-long
  immutable lifetime that failure is both silent and very long-lived.
* **Handing off to the web server.** Once the access check has passed, Apache
  can send the file itself via ``X-Sendfile``, costing the Python process
  nothing. `XSendFilePath` lets Apache serve a file *the application asked it
  to*; it is not a route by which a request can reach one.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from flask import Response, request, send_file

from . import derive
from .config import Config

log = logging.getLogger("harelphotos.images")

# A year. Every image URL carries the recipe fingerprint, so a regenerated
# photo is a different URL and this can be as long as we like.
IMMUTABLE = "private, max-age=31536000, immutable"

FORMAT_MIME = {"avif": "image/avif", "webp": "image/webp", "jpeg": "image/jpeg"}


def acceptable_format(cfg: Config, accept: str | None) -> str:
    """Pick the best format this client can read.

    AVIF is what we pre-generate and what every current browser accepts; the
    rest is a safety net that costs nothing when unused.
    """
    if cfg.encode.fallback == "none":
        return cfg.encode.format
    accept = accept or ""
    for fmt in (cfg.encode.format, "avif", "webp"):
        if FORMAT_MIME.get(fmt, "") in accept:
            return fmt
    return "jpeg"


def transcode_for_fallback(cfg: Config, tier: int, relpath: str, fmt: str) -> Path | None:
    """Make a WebP or JPEG copy of an already-generated tier, and cache it.

    On demand rather than pre-generated: a parallel tree for every format would
    double the disk to serve almost nobody. The first request from such a
    client is slow; every one after it is a static file.
    """
    source = derive.derived_path(cfg, tier, relpath, cfg.encode.format)
    if not source.is_file():
        return None
    dest = cfg.derived_root / fmt / str(tier) / f"{relpath}.{fmt}"
    if dest.is_file():
        return dest
    try:
        from PIL import Image

        with Image.open(source) as im:
            im = im.convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"{dest.name}.tmp")
            if fmt == "webp":
                im.save(tmp, "WEBP", quality=cfg.encode.quality_for(tier) + 20, method=4)
            else:
                im.save(tmp, "JPEG", quality=82, optimize=True, progressive=True)
            tmp.replace(dest)
        log.info("transcoded %s tier %d to %s for a client without %s",
                 relpath, tier, fmt, cfg.encode.format)
        return dest
    except Exception as e:
        log.warning("cannot transcode %s to %s: %s", source, fmt, e)
        return None


def resolve(cfg: Config, tier: int, relpath: str) -> tuple[Path, str] | None:
    """Find the file to send for one tier, negotiating format."""
    want = acceptable_format(cfg, request.headers.get("Accept"))
    primary = derive.derived_path(cfg, tier, relpath, cfg.encode.format)
    if want == cfg.encode.format:
        return (primary, cfg.encode.format) if primary.is_file() else None
    fallback = transcode_for_fallback(cfg, tier, relpath, want)
    if fallback is not None:
        return fallback, want
    return (primary, cfg.encode.format) if primary.is_file() else None


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def send(cfg: Config, path: Path, mime: str, *, download_name: str | None = None,
         immutable: bool = True, vary_accept: bool = True) -> Response:
    """Send a file, handing off to the web server where possible."""
    # X-Sendfile only for the generated tree.
    #
    # Apache will send a file the application names only if it sits under an
    # XSendFilePath, and that lists the derived directory alone -- the photo
    # tree cannot be added, because it lives in a home directory Apache cannot
    # read, which is the whole reason the generated images live elsewhere.
    #
    # Naming a file outside that list does not fail loudly: mod_xsendfile
    # answers 404. So every original -- the "Download original" button, and the
    # full-size image shown for a photo whose copies are not generated yet --
    # was silently missing in production while working perfectly in
    # development, where nothing hands off to Apache at all.
    if cfg.sendfile_header == "X-Sendfile" and _under(path, cfg.derived_root):
        resp = Response(b"", mimetype=mime)
        resp.headers["X-Sendfile"] = str(path)
        resp.headers["Content-Length"] = str(path.stat().st_size)
    else:
        resp = send_file(
            path,
            mimetype=mime,
            as_attachment=download_name is not None,
            download_name=download_name,
            conditional=download_name is None,
        )
    if download_name is not None:
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{download_name}"'
        )
        resp.headers["Cache-Control"] = "private, max-age=3600"
    elif immutable:
        resp.headers["Cache-Control"] = IMMUTABLE
    if vary_accept and cfg.encode.fallback != "none":
        # Mandatory: this URL can return three different formats.
        resp.headers["Vary"] = "Accept"
    return resp


def original_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
