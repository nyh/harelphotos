"""The web application (DESIGN.md 10, 12).

Login is enforced by a single `before_request` hook against a literal list of
public endpoints (`auth.PUBLIC_ENDPOINTS`), and a test walks every registered
route to check nothing escaped it.

`require_login=False` turns the gate off entirely, for working on the interface
locally without typing a password every time. It is a per-process argument, not
a config setting: something that disables authentication should have to be
passed deliberately, and never be a line in a file that could be wrong on the
server.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Flask, abort, flash, g, get_flashed_messages, redirect, render_template,
    request, session, url_for,
)
from markupsafe import Markup, escape

from . import auth, db, google_auth, images, overrides, public_assets
from . import users as users_mod
from .config import Config
from .queries import Album, Index, Photo, Viewer

log = logging.getLogger("harelphotos.web")

# The viewer used when the login gate is off: sees everything, like an admin,
# but has no token, so the chrome can tell the difference and say so.
NO_LOGIN_VIEWER = Viewer(token=None, name="", is_admin=True)


def create_app(cfg: Config, *, require_login: bool = True) -> Flask:
    app = Flask(__name__)
    if cfg.behind_proxy:
        # Behind Apache every request otherwise appears to come from 127.0.0.1
        # over plain HTTP: the login throttle would count the proxy rather than
        # the client, and any absolute URL we build would say http://.
        #
        # One hop only, and only when configured -- believing these headers on
        # a directly-reachable server lets anyone forge their own address.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["HARELPHOTOS"] = cfg
    app.config["HARELPHOTOS_REQUIRE_LOGIN"] = require_login
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.secret_key = auth.secret_key(cfg)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Off only when serving plain HTTP locally: with it on and no TLS the
        # cookie is never sent back, and you land on the login page again with
        # no error anywhere (DESIGN.md 13.5).
        SESSION_COOKIE_SECURE=cfg.base_url.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=cfg.session_days),
        # Send the cookie only when the session actually changes.
        #
        # Flask's default re-signs and re-sends it on *every* response, so its
        # value differs each time. Image responses carry `Vary: Cookie` --
        # Flask adds that to anything that touched the session, and the access
        # check always does -- which makes the cookie part of the browser's
        # cache key. A value that changes every response therefore invalidated
        # every cached thumbnail on every page load, and the whole grid was
        # re-downloaded each time it was displayed.
        #
        # On localhost that was invisible. Over HTTPS with real latency it was
        # seconds of grey placeholders on every back-navigation.
        #
        # The cost is that the 30-day expiry no longer slides on each request:
        # it now runs from the moment of login. For a family album that is a
        # fair trade for a grid that paints instantly.
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    @app.before_request
    def _open_index() -> None:
        g.conn = db.open_index(cfg.index_db, read_only=True)
        g.index = Index(g.conn, cfg)
        g.viewer = NO_LOGIN_VIEWER if not require_login else auth.current_viewer(cfg)

    @app.before_request
    def _require_login():
        """The login gate. Fail closed: anything not listed needs a session."""
        if not require_login:
            return None
        if request.endpoint in auth.PUBLIC_ENDPOINTS:
            return None
        if g.viewer is not None:
            return None
        if request.endpoint is None:
            abort(404)
        # An image or JSON request from a stale page should not be answered
        # with a login page; say plainly that it is unauthorised.
        if request.endpoint in ("image", "original", "inline_original", "api_album"):
            abort(401)
        return redirect(auth.login_url(cfg))

    @app.teardown_request
    def _close_index(exc) -> None:
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def _chrome_context():
        return {
            "viewer": g.get("viewer"),
            "dev_no_login": not app.config["HARELPHOTOS_REQUIRE_LOGIN"],
        }

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

    # Prepare the landing image and home-screen icons here rather than in the
    # `serve` command, so they exist however the application was started.
    # Under gunicorn nothing else ever calls this, and the result was a
    # configured landing_image that simply never appeared in production.
    #
    # Cheap and idempotent: anything already on disk is skipped.
    try:
        made = public_assets.build(cfg) + public_assets.build_icons(cfg)
        if made:
            log.info("prepared public assets: %s", ", ".join(made))
    except Exception as e:                       # never fail to start over a picture
        log.warning("could not prepare the public assets: %s", e)

    _register_routes(app, cfg)
    _register_filters(app, cfg)
    return app


def _paginate(cfg: Config, total: int, raw_page: str | None) -> dict:
    """Split a very large album into pages.

    A directory of several thousand photos is one page of several megabytes and
    tens of thousands of DOM nodes, which a phone feels. Most albums are far
    below the limit and get a single page with no pager shown at all.
    """
    size = max(1, cfg.ui.album_page_size)
    pages = max(1, -(-total // size))           # ceiling division
    try:
        page = int(raw_page) if raw_page else 1
    except ValueError:
        page = 1
    page = min(max(1, page), pages)
    return {
        "page": page,
        "pages": pages,
        "size": size,
        "total": total,
        "start": (page - 1) * size,
        "end": min(total, page * size),
    }


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
    def landing():
        """The only page an unauthenticated visitor sees (DESIGN.md 11.5)."""
        if g.viewer is not None:
            return redirect("/a/")
        return _render_landing(cfg)

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if g.viewer is not None and request.method == "GET":
            return redirect(auth.safe_next(request.args.get("next")) or "/a/")
        if request.method == "GET":
            return _render_landing(cfg, next_url=auth.safe_next(request.args.get("next")))

        nxt = auth.safe_next(request.form.get("next"))
        if not auth.check_csrf():
            # Usually a stale form rather than an attack; say something useful.
            return _render_landing(
                cfg, next_url=nxt,
                error="That form had expired. Please try again.",
            ), 400

        # The throttle has its own small database: it is the only thing the
        # web process writes, and in the index it collided with a running scan
        # (DESIGN.md 12.1).
        with auth.throttle_db(cfg) as throttle:
            try:
                auth.check_rate_limit(throttle)
            except auth.LoginRateLimited as e:
                return _render_landing(
                    cfg, next_url=nxt,
                    error=f"Too many attempts. Try again in {e.wait:.0f} seconds.",
                ), 429

            username = (request.form.get("username") or "").strip()
            user = auth.authenticate(cfg, username, request.form.get("password") or "")
            if user is None:
                auth.record_failure(throttle)
                log.info("failed login for %r from %s", username, request.remote_addr)
                # Never says which of the two was wrong, nor whether the
                # account exists.
                return _render_landing(
                    cfg, next_url=nxt, error="Incorrect username or password.",
                ), 401
            auth.clear_failures(throttle)

        auth.log_in(user, via="local")
        log.info("login: %s from %s", user.token, request.remote_addr)
        return redirect(nxt or "/a/")

    @app.route("/auth/google")
    def google_start():
        """Send the browser to Google. GET is correct here: it navigates
        away and changes nothing until the callback comes back."""
        if not google_auth.enabled(cfg):
            abort(404)
        if g.viewer is not None:
            return redirect("/a/")
        nxt = auth.safe_next(request.args.get("next"))
        return redirect(google_auth.begin(cfg, session, nxt))

    @app.route("/auth/google/callback")
    def google_callback():
        if not google_auth.enabled(cfg):
            abort(404)
        # Google reports a refusal here rather than at the token endpoint;
        # the commonest is the person pressing Cancel, which is not an error.
        if request.args.get("error"):
            log.info("google sign-in declined: %s", request.args.get("error"))
            return _render_landing(cfg, error="Sign-in with Google was cancelled."), 400
        try:
            identity, nxt = google_auth.complete(
                cfg,
                session,
                code=request.args.get("code", ""),
                state=request.args.get("state", ""),
            )
        except google_auth.GoogleAuthError as e:
            # The detail goes to the log, not to the page: it can name the
            # client secret or the mismatched redirect URI.
            log.warning("google sign-in failed: %s", e)
            return _render_landing(cfg, error="Could not sign in with Google."), 400

        # Authentication is not authorization. Google has told us who this is;
        # whether they may look at anything is still decided by users.toml.
        user = users_mod.load(cfg.users_file).by_google_email(identity.email)
        if user is None:
            log.info("google sign-in by %s, who has no account", identity.email)
            return _render_landing(
                cfg,
                error=f"{identity.email} has not been invited to this album.",
            ), 403

        auth.log_in(user, via="google")
        log.info("login: %s via google from %s", user.token, request.remote_addr)
        return redirect(auth.safe_next(nxt) or "/a/")

    @app.route("/cover", methods=("POST",))
    def set_cover():
        """Choose the photo shown on an album's card.

        Admin only, and POST only with a CSRF token: this changes what everyone
        sees, so it must not be something a link can do to you.
        """
        if not (g.viewer and g.viewer.is_admin):
            abort(403)
        if not auth.check_csrf():
            abort(400)
        relpath = _clean_path(request.form.get("album", ""))
        name = request.form.get("photo", "")
        clear = bool(request.form.get("clear"))

        alb = g.index.album(relpath, g.viewer)
        if alb is None:
            abort(404)
        if not clear:
            # A file name, or a path relative to this album -- the latter is
            # the only way to give a cover to a directory that holds nothing
            # but subdirectories. Resolved against the index and against this
            # viewer, so a pick can only name a photo that exists and that they
            # are allowed to see.
            name = name.strip("/")
            if not name or ".." in name.split("/"):
                abort(404)
            if g.index.photo(f"{relpath}/{name}" if relpath else name,
                             g.viewer) is None:
                abort(404)
        try:
            overrides.set_for(cfg, relpath, cover=None if clear else name)
        except overrides.OverrideError as e:
            log.warning("cannot record a cover pick: %s", e)
            abort(500)
        log.info("cover for %s set to %s by %s",
                 relpath or "/", "auto" if clear else name, g.viewer.token)
        return redirect(auth.safe_next(request.form.get("next")) or alb.url)

    @app.route("/logout", methods=("POST",))
    def logout():
        """POST only, and CSRF-checked.

        A GET link would be followed by link prefetchers, mail scanners and
        preview bots, each of which would silently log you out.
        """
        if not auth.check_csrf():
            abort(400)
        auth.log_out()
        return redirect(url_for("landing", logged_out=1))

    @app.route("/privacy")
    def privacy():
        return _render_text(cfg, "privacy.md", "Privacy")

    @app.route("/terms")
    def terms():
        return _render_text(cfg, "terms.md", "Terms")

    @app.route("/manifest.webmanifest")
    def manifest():
        """Public by necessity: the browser fetches this before anyone has
        signed in, and it holds nothing but the site's name and icons."""
        return app.response_class(
            json.dumps(public_assets.manifest(cfg), ensure_ascii=False, indent=1),
            mimetype="application/manifest+json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.route("/public/<name>")
    def public_asset(name: str):
        """Fixed filenames only — no part of the request becomes a path."""
        path = public_assets.asset_path(cfg, name)
        if path is None or not path.is_file():
            abort(404)
        mime = "image/avif" if name.endswith(".avif") else "image/jpeg"
        return images.send(cfg, path, mime, vary_accept=False)

    @app.route("/a/")
    @app.route("/a/<path:dirpath>/")
    def album(dirpath: str = ""):
        path = _clean_path(dirpath)
        alb = g.index.album(path, g.viewer)
        if alb is None:
            abort(404)
        subalbums = g.index.subalbums(alb, g.viewer)
        photos = g.index.photos(alb, g.viewer)
        pager = _paginate(cfg, len(photos), request.args.get("page"))
        return render_template(
            "album.html",
            album=alb,
            subalbums=subalbums,
            photos=photos[pager["start"]:pager["end"]],
            pager_data=pager,
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
        # Which page of a paged album this photo sits on, so "back to the
        # album" returns to the right one rather than always the first.
        album_url = alb.url if alb else "/a/"
        if alb is not None:
            siblings = g.index.photos(alb, g.viewer)
            size = max(1, cfg.ui.album_page_size)
            if len(siblings) > size:
                for i, sib in enumerate(siblings):
                    if sib.id == pho.id:
                        page = i // size + 1
                        if page > 1:
                            album_url = f"{album_url}?page={page}"
                        break
        return render_template(
            "photo.html",
            photo=pho,
            album=alb,
            album_url=album_url,
            # Whether this photo is the album's chosen cover, so the button
            # can offer to undo instead of repeating what is already true.
            is_cover=(alb is not None
                      and overrides.get(cfg, alb.path).cover == pho.name),
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

    @app.route("/api/a/", defaults={"dirpath": ""})
    @app.route("/api/a/<path:dirpath>/")
    def api_album(dirpath: str = ""):
        """Photo list as JSON, for the large-album safety valve and prefetch."""
        alb = g.index.album(_clean_path(dirpath), g.viewer)
        if alb is None:
            abort(404)
        photos = g.index.photos(alb, g.viewer)
        return {
            "path": alb.path,
            "count": len(photos),
            "photos": [
                {"name": p.name, "url": p.page_url, "aspect": round(p.aspect, 4)}
                for p in photos
            ],
        }

    @app.route("/healthz")
    def healthz():
        return {"ok": True, "photos": g.conn.execute(
            "SELECT count(*) AS n FROM photos").fetchone()["n"]}

    @app.errorhandler(404)
    def not_found(_):
        return render_template("404.html", cfg=cfg), 404


def _render_landing(cfg: Config, next_url: str | None = None, error: str | None = None):
    notice = None
    if request.args.get("logged_out"):
        notice = "You have been logged out."
    return render_template(
        "landing.html",
        cfg=cfg,
        hero=public_assets.hero(cfg),
        csrf=auth.csrf_token(),
        next_url=next_url,
        error=error,
        notice=notice,
    )


def _render_text(cfg: Config, filename: str, heading: str):
    """Serve the privacy/terms text that `init` wrote, as plain paragraphs."""
    path = (cfg.source.parent if cfg.source else Path(".")) / filename
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raw = f"# {heading}\n\nNot configured."
    html = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("# "):
            html.append(f"<h1>{escape(block[2:].strip())}</h1>")
        elif block.startswith("- "):
            # A bullet may wrap onto following indented lines, and those
            # continuations belong to the item above them. Dropping any line
            # that did not itself begin with "- " truncated every wrapped
            # bullet mid-sentence -- silently, in the privacy policy.
            items: list[str] = []
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip())
                elif items:
                    items[-1] += " " + stripped
                else:
                    items.append(stripped)
            html.append(
                "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"
            )
        else:
            html.append(f"<p>{escape(block)}</p>")
    return render_template(
        "text.html", cfg=cfg, heading=heading, body=Markup("".join(html))
    )


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

    @app.template_filter("day_date")
    def day_date(ts: int | None) -> str:
        """For the photo's top line: the day, without the weekday or the time.

        `photo_date` is too long to sit beside a filename and `short_date` too
        coarse to be worth showing there. Local wall-clock as recorded, never
        converted through UTC (DESIGN.md 11.2).
        """
        return datetime.fromtimestamp(ts).strftime("%-d %b %Y") if ts else ""

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
            parts.append(f"/i/{tier}/{photo.url_relpath}?v={photo.deriv_key} {w}w")
        return ", ".join(parts)

    @app.template_filter("view_sizes")
    def view_sizes(photo: Photo) -> str:
        """How wide the single photo will actually render.

        Not 100vw. The photo is letterboxed to fit *inside* the window, so for
        anything but a very tall image the limit is the available HEIGHT, not
        the width: measured in Chrome, a 4:3 photo in a 1920x1080 window
        renders 1079 px wide, not 1900. Claiming 100vw made the browser fetch
        the 2048 tier where 1280 was plenty — wasted bandwidth on every photo
        viewed.
        """
        ar = max(0.2, min(5.0, photo.aspect))
        # An upper bound, not an exact figure, and deliberately so.
        #
        # The CSS clamps the photo to the space left over, so this no longer
        # decides how big it appears -- only which file is fetched. Erring
        # slightly large therefore costs at most a tier, and never upscales.
        # Erring *small* used to be the problem: the browser laid the image out
        # at exactly what this claimed, so JavaScript had to measure and correct
        # it afterwards, and the photo visibly grew a moment after appearing.
        #
        # Being a CSS expression in viewport units, the browser re-evaluates it
        # on resize and rotation by itself. No JavaScript is involved at all.
        #
        # dvh, not vh: on a phone vh is the height with the toolbar hidden,
        # which overstates the space by around 10%.
        return f"min(100vw, calc(100dvh * {ar:.3f}))"

    @app.template_filter("grid_sizes")
    def grid_sizes(photo: Photo) -> str:
        """How wide this tile will actually be.

        A justified row scales every tile to the row height, so the width is
        the aspect ratio times that height — NOT a single figure shared by
        every photo. Telling the browser a flat "180px" makes it fetch a 256 px
        file for a tile that renders 540 px wide, and the upscaling is
        obvious. app.js overwrites this with the exact value once it has laid
        the rows out; this is the honest estimate until then, and the correct
        value for the no-JS fallback, whose CSS uses the same arithmetic.
        """
        ar = max(0.4, min(3.0, photo.aspect))
        return (
            f"(max-width: 600px) {round(ar * 130)}px, {round(ar * 180)}px"
        )

    @app.template_filter("img_src")
    def img_src(photo: Photo, want: int) -> str:
        tier = photo.tier_for(want)
        if tier is None:
            # No derivative at all: fall back to the original, inline.
            return f"/i/orig/{photo.url_relpath}"
        return f"/i/{tier}/{photo.url_relpath}?v={photo.deriv_key}"

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
    # The grid offers EVERY generated tier, not just the "thumbnail" ones.
    # A justified row makes a wide photo far wider than the row is tall — a
    # 3:1 panorama at 180 px tall is 540 CSS px, or 1080 device px on a retina
    # screen — so a ladder stopping at 512 would visibly upscale it. Offering
    # the larger tiers costs nothing: they already exist, and `sizes` stops a
    # small tile from ever fetching one.
    app.jinja_env.globals["csrf_token"] = auth.csrf_token
    app.jinja_env.globals["all_tiers"] = sorted(cfg.sizes.tiers)
    app.jinja_env.globals["thumb_tiers"] = list(cfg.sizes.thumb)
    app.jinja_env.globals["view_tiers"] = sorted(cfg.sizes.view)
    # Whether an apple-touch-icon exists to point at. Read once at startup:
    # the icons are written by `init`/`scan`, not while serving.
    app.jinja_env.globals["app_icons"] = bool(public_assets.icons(cfg))


def wsgi_app(config_path: str | Path | None = None) -> Flask:
    """Entry point for gunicorn: `harelphotos.web:app`."""
    from . import config as config_mod

    return create_app(config_mod.load(config_path))
