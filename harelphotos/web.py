"""The web application (DESIGN.md 10).

M4: browsing, with no authentication — every request is treated as an admin.
M5 replaces `current_viewer()` with the session and adds the login gate; the
access-control machinery it will use is already in place and already applied to
every query, so that change is a substitution rather than a retrofit.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, url_for

from . import db, images
from .config import Config
from .queries import Album, Index, Photo, Viewer

log = logging.getLogger("harelphotos.web")

# Until M5 there is no session, and the app binds to localhost only.
DEV_VIEWER = Viewer(token=None, name="", is_admin=True)


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    app.config["HARELPHOTOS"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    @app.before_request
    def _open_index() -> None:
        g.conn = db.open_index(cfg.index_db, read_only=True)
        g.index = Index(g.conn, cfg)
        g.viewer = current_viewer()

    @app.teardown_request
    def _close_index(exc) -> None:
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
        )
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    _register_routes(app, cfg)
    _register_filters(app, cfg)
    return app


def current_viewer() -> Viewer:
    return DEV_VIEWER


def _clean_path(raw: str) -> str:
    """Reject anything that is not a plain relative path.

    Belt and braces: paths are resolved by exact database lookup, so traversal
    cannot reach the filesystem anyway (DESIGN.md 10.2). This just refuses the
    obviously-malicious early.
    """
    path = (raw or "").strip("/")
    if not path:
        return ""
    for part in path.split("/"):
        if part in ("", ".", "..") or "\0" in part:
            abort(404)
    return path


def _register_routes(app: Flask, cfg: Config) -> None:
    @app.route("/")
    def home():
        # Not url_for: the <path:> converter renders an empty segment as "/a//".
        return redirect("/a/")

    @app.route("/a/")
    @app.route("/a/<path:dirpath>/")
    def album(dirpath: str = ""):
        path = _clean_path(dirpath)
        alb = g.index.album(path, g.viewer)
        if alb is None:
            abort(404)
        subalbums = g.index.subalbums(alb, g.viewer)
        photos = g.index.photos(alb, g.viewer)
        return render_template(
            "album.html",
            album=alb,
            subalbums=subalbums,
            photos=photos,
            crumbs=g.index.breadcrumbs(alb, g.viewer),
            cfg=cfg,
        )

    @app.route("/p/<path:photopath>")
    def photo(photopath: str):
        path = _clean_path(photopath)
        pho = g.index.photo(path, g.viewer)
        if pho is None:
            abort(404)
        alb = g.index.album(pho.dir_path, g.viewer)
        prev_p, next_p = g.index.neighbours(pho, g.viewer)
        return render_template(
            "photo.html",
            photo=pho,
            album=alb,
            prev=prev_p,
            next=next_p,
            crumbs=g.index.breadcrumbs(alb, g.viewer) + [alb] if alb else [],
            cfg=cfg,
        )

    @app.route("/i/<int:tier>/<path:photopath>")
    def image(tier: int, photopath: str):
        path = _clean_path(photopath)
        if tier not in cfg.sizes.tiers:
            abort(404)
        # The access check happens here, before any file is named. A private
        # album's thumbnails must not be fetchable by guessing URLs.
        if g.index.photo(path, g.viewer) is None:
            abort(404)
        found = images.resolve(cfg, tier, path)
        if found is None:
            abort(404)
        file_path, fmt = found
        return images.send(cfg, file_path, images.FORMAT_MIME[fmt])

    @app.route("/i/orig/<path:photopath>")
    def inline_original(photopath: str):
        """A photo smaller than every tier has no derivative, but must still
        display. Same bytes as the download route, without the attachment
        disposition, which would offer a download instead of showing it."""
        path = _clean_path(photopath)
        pho = g.index.photo(path, g.viewer)
        if pho is None:
            abort(404)
        return images.send(
            cfg, cfg.photo_root / path, images.original_mime(pho.name), vary_accept=False
        )

    @app.route("/orig/<path:photopath>")
    def original(photopath: str):
        path = _clean_path(photopath)
        pho = g.index.photo(path, g.viewer)
        if pho is None:
            abort(404)
        return images.send(
            cfg,
            cfg.photo_root / path,
            images.original_mime(pho.name),
            download_name=pho.name,
            vary_accept=False,
        )

    @app.route("/healthz")
    def healthz():
        return {"ok": True, "photos": g.conn.execute(
            "SELECT count(*) AS n FROM photos").fetchone()["n"]}

    @app.errorhandler(404)
    def not_found(_):
        return render_template("404.html", cfg=cfg), 404


def _register_filters(app: Flask, cfg: Config) -> None:
    @app.template_filter("photo_date")
    def photo_date(ts: int | None) -> str:
        """EXIF time is local wall-clock with no zone; render it as recorded
        and never convert through UTC (DESIGN.md 11.2)."""
        if not ts:
            return ""
        return datetime.fromtimestamp(ts).strftime("%A, %-d %B %Y, %H:%M")

    @app.template_filter("short_date")
    def short_date(ts: int | None) -> str:
        return datetime.fromtimestamp(ts).strftime("%b %Y") if ts else ""

    @app.template_filter("date_span")
    def date_span(album: Album) -> str:
        lo, hi = album.date_min, album.date_max
        if not lo and not hi:
            return ""
        a, b = short_date(lo), short_date(hi)
        return a if a == b else f"{a} – {b}"

    @app.template_filter("filesize")
    def filesize(n: int | None) -> str:
        if not n:
            return ""
        if n >= 1 << 20:
            return f"{n / (1 << 20):.1f} MB"
        return f"{n / 1024:.0f} KB"

    @app.template_filter("srcset")
    def srcset(photo: Photo, tiers: list[int]) -> str:
        """`w` descriptors must be the real per-photo width, not the tier."""
        parts = []
        for tier in tiers:
            if tier not in photo.tiers:
                continue
            w, _ = photo.size_at(tier)
            parts.append(f"/i/{tier}/{photo.relpath}?v={photo.deriv_key} {w}w")
        return ", ".join(parts)

    @app.template_filter("img_src")
    def img_src(photo: Photo, want: int) -> str:
        tier = photo.tier_for(want)
        if tier is None:
            # No derivative at all: fall back to the original, inline.
            return f"/i/orig/{photo.relpath}"
        return f"/i/{tier}/{photo.relpath}?v={photo.deriv_key}"

    @app.template_filter("exposure")
    def exposure(exif: dict) -> str:
        """The conventional photographer's line; omit what is absent."""
        bits = []
        t = exif.get("exposure")
        if t:
            bits.append(f"1/{round(1 / t)} s" if t < 1 else f"{t:g} s")
        if exif.get("fnumber"):
            bits.append(f"f/{exif['fnumber']:g}")
        if exif.get("iso"):
            bits.append(f"ISO {exif['iso']}")
        if exif.get("focal"):
            bits.append(f"{exif['focal']:g} mm")
        return " · ".join(bits)

    @app.template_filter("maplink")
    def maplink(exif: dict) -> str | None:
        if cfg.ui.map_link == "none" or not cfg.ui.show_gps:
            return None
        lat, lon = exif.get("lat"), exif.get("lon")
        if lat is None or lon is None:
            return None
        if cfg.ui.map_link == "google":
            return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}"

    app.jinja_env.globals["cfg"] = cfg
    app.jinja_env.globals["thumb_tiers"] = list(cfg.sizes.thumb)
    app.jinja_env.globals["view_tiers"] = list(cfg.sizes.view)


def wsgi_app(config_path: str | Path | None = None) -> Flask:
    """Entry point for gunicorn: `harelphotos.web:app`."""
    from . import config as config_mod

    return create_app(config_mod.load(config_path))
