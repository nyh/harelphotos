"""Serving image bytes.

Two things happen here that are easy to get subtly wrong:

* **Content negotiation.** One URL returns AVIF, WebP or JPEG depending on what
  the browser said it accepts. That makes ``Vary: Accept`` mandatory — without
  it a cache can hand a client a format it cannot read, and with a year-long
  immutable lifetime that failure is both silent and very long-lived.
* **Every byte goes through Python.** This used to be optional: once the access
  check had passed, Apache could send the file itself via ``X-Sendfile``. That
  hand-off was removed in September 2026 after measuring it against the live
  server, where it was *slower* — see ``send`` below.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from flask import Response, abort, request, send_file

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


def _within_a_served_root(cfg: Config, path: Path) -> bool:
    """Is this file one this software is ever allowed to send?

    Belt and braces. Every route resolves a path by exact lookup in the index
    before reaching here, so nothing outside the photo tree can be named in the
    first place -- but Apache used to refuse anything outside XSendFilePath,
    and that accidental second wall went when originals stopped being handed to
    it. This is the deliberate replacement, so a future route that forgets the
    index lookup fails closed rather than serving the filesystem.
    """
    return _under(path, cfg.photo_root) or _under(path, cfg.derived_root)


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def send(cfg: Config, path: Path, mime: str, *, download_name: str | None = None,
         immutable: bool = True, vary_accept: bool = True) -> Response:
    """Send a file. Python copies the bytes; nothing is handed to the proxy.

    There used to be an `X-Sendfile` hand-off here, so that Apache sent the
    derived images itself and the Python process only did the access check. It
    was removed after measuring it on the live server -- a weak CPU behind a
    slow link, exactly the machine it was supposed to help:

        24 thumbnails (344 KB)     X-Sendfile      Python
        over HTTP/2                  6.56 s        0.84 s
        over HTTP/1.1                2.52 s        0.87 s
        240 sequential, stalled       38%             0%

    The mechanism was never identified, so this is not a claim that the
    technique is wrong -- nginx's `X-Accel-Redirect` is the same idea and is
    not suspected. What can be said: the only Apache implementation is
    `mod_xsendfile`, a third-party module last released around 2012 that does
    not officially support Apache 2.4, let alone HTTP/2; it was slow over
    HTTP/1.1 too, so it is not purely an h2 interaction; and the hand-off's
    entire purpose -- sparing the CPU the byte copying -- buys nothing here,
    because Python was measured moving image bytes at 2.4 MB/s on that box,
    which is faster than the link it feeds.

    So the best case was zero and the measured case was a 7.8x loss, and the
    `sendfile_header` setting went with it.
    """
    if not _within_a_served_root(cfg, path):
        log.warning("refusing to send %s: outside the photo and derived trees", path)
        abort(404)
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
