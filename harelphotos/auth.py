"""Sessions, login and the login gate (DESIGN.md 12).

The security-critical module. Three things it must get right:

* **Fail closed.** Every route requires a session unless it appears on a short,
  literal exemption list. A test walks every registered route and asserts
  exactly that, so a new route cannot quietly forget.
* **Authentication is not authorization.** A verified identity is only ever
  matched against `users.toml`; nothing self-registers, and access to a
  particular album is then decided by the ACL chain (§6).
* **Don't leak which accounts exist.** An unknown username costs the same time
  as a known one, and says the same thing.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import contextlib
import hmac
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from flask import g, redirect, request, session, url_for

from . import users as users_mod
from .config import Config
from .queries import Viewer

log = logging.getLogger("harelphotos.auth")

SESSION_USER = "u"
SESSION_EPOCH = "e"
SESSION_VIA = "v"
CSRF_KEY = "csrf"

# Routes reachable without a session. Deliberately a literal list of endpoint
# names rather than a pattern: this is the highest-risk handful of lines in the
# application, and it should be possible to read it and be sure.
PUBLIC_ENDPOINTS = frozenset(
    {
        "landing",          # the front page, for people who are not logged in
        "login",
        "google_start",     # signing in cannot require being signed in
        "google_callback",
        "logout",           # clearing a session needs no session
        "privacy",          # Google's consent screen requires these to be
        "terms",            # publicly fetchable (§12.2)
        "public_asset",     # the landing page's own image, fixed filenames
        "manifest",         # fetched before anyone has signed in
        "healthz",
        "static",
    }
)

# Login throttling. Slow enough to make guessing pointless, generous enough
# that a relative mistyping their password three times is not locked out.
MAX_FAILURES_BEFORE_DELAY = 3
MAX_DELAY_SECONDS = 30.0


class LoginRateLimited(Exception):
    def __init__(self, wait: float) -> None:
        super().__init__(f"try again in {wait:.0f} seconds")
        self.wait = wait


def secret_key(cfg: Config) -> bytes:
    """Read the signing key. Changing it logs everyone out."""
    try:
        data = cfg.secret_key_file.read_bytes()
    except OSError as e:
        raise RuntimeError(
            f"cannot read {cfg.secret_key_file}: {e}. Run 'harelphotos init'."
        ) from e
    if len(data) < 16:
        raise RuntimeError(f"{cfg.secret_key_file} is too short to be a signing key")
    return data


# ------------------------------------------------------------------ viewer

def current_viewer(cfg: Config) -> Viewer | None:
    """Who the session says is asking, or None.

    Returns None — not an anonymous Viewer — when the cookie names a user who
    has since been deleted, or whose `epoch` has been bumped to revoke their
    sessions. Both must stop working immediately.
    """
    token = session.get(SESSION_USER)
    if not token:
        return None
    us = users_mod.load(cfg.users_file)
    user = us.get(token)
    if user is None:
        session.clear()
        return None
    if int(session.get(SESSION_EPOCH, 0)) != user.epoch:
        log.info("session for %s rejected: epoch %s != %s",
                 token, session.get(SESSION_EPOCH), user.epoch)
        session.clear()
        return None
    return Viewer(token=user.token, name=user.name, is_admin=user.admin)


def log_in(user: users_mod.User, via: str = "local") -> None:
    session.clear()                 # never reuse a session id across logins
    session[SESSION_USER] = user.token
    session[SESSION_EPOCH] = user.epoch
    session[SESSION_VIA] = via
    session.permanent = True
    new_csrf()


def log_out() -> None:
    session.clear()


# -------------------------------------------------------------------- CSRF

def new_csrf() -> str:
    token = secrets.token_urlsafe(32)
    session[CSRF_KEY] = token
    return token


def csrf_token() -> str:
    return session.get(CSRF_KEY) or new_csrf()


def check_csrf() -> bool:
    sent = request.form.get("csrf", "")
    have = session.get(CSRF_KEY, "")
    return bool(have) and hmac.compare_digest(sent, have)


# ------------------------------------------------------------- throttling
#
# This lives in its own small database rather than in the index.
#
# It is the only thing the web process writes, and SQLite permits exactly one
# writer at a time: in the index it collided with a running scan and logging in
# failed outright with "database is locked". Its own file removes the
# contention entirely, and leaves the index genuinely read-only to the web
# process, which was always the intent.
#
# Every operation here is also best-effort. Throttling is a precaution;
# refusing a correct password because a counter could not be written would be a
# far worse failure than not counting it.

AUTH_DB = "auth.sqlite"
BUSY_TIMEOUT_MS = 3000

_AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS login_attempts (
  key      TEXT PRIMARY KEY,
  failures INTEGER NOT NULL DEFAULT 0,
  last_try INTEGER NOT NULL DEFAULT 0
);
"""


def _key() -> str:
    return f"{request.remote_addr or '?'}|{request.form.get('username', '')}"


@contextlib.contextmanager
def throttle_db(cfg: Config):
    """Open the throttle database, or yield None if it cannot be opened."""
    conn = None
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(cfg.state_dir / AUTH_DB, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.executescript(_AUTH_SCHEMA)
        yield conn
    except sqlite3.Error as e:
        log.warning("login throttle unavailable (%s); continuing without it", e)
        yield None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def check_rate_limit(conn) -> None:
    if conn is None:
        return
    try:
        row = conn.execute(
            "SELECT failures, last_try FROM login_attempts WHERE key = ?", (_key(),)
        ).fetchone()
    except sqlite3.Error as e:
        log.warning("cannot read the login throttle: %s", e)
        return
    if row is None or row["failures"] < MAX_FAILURES_BEFORE_DELAY:
        return
    # Exponential, capped: 2s, 4s, 8s ... 30s.
    delay = min(MAX_DELAY_SECONDS, 2.0 ** (row["failures"] - MAX_FAILURES_BEFORE_DELAY + 1))
    waited = time.time() - row["last_try"]
    if waited < delay:
        raise LoginRateLimited(delay - waited)


def record_failure(conn) -> None:
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO login_attempts (key, failures, last_try) VALUES (?, 1, ?) "
            "ON CONFLICT(key) DO UPDATE SET failures = failures + 1, "
            "last_try = excluded.last_try",
            (_key(), int(time.time())),
        )
        conn.commit()
    except sqlite3.Error as e:
        log.warning("cannot record a failed login: %s", e)


def clear_failures(conn) -> None:
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM login_attempts WHERE key = ?", (_key(),))
        conn.commit()
    except sqlite3.Error as e:
        # Never fatal: the login itself has already succeeded.
        log.warning("cannot clear the login throttle: %s", e)


# ------------------------------------------------------------ authenticate

def authenticate(cfg: Config, username: str, password: str) -> users_mod.User | None:
    """Check a username and password.

    An unknown username still costs a full password hash comparison, so the
    response time does not reveal which accounts exist.
    """
    us = users_mod.load(cfg.users_file)
    user = us.get(username)
    if user is None or not user.can_login_locally:
        users_mod.waste_time_like_a_real_check(password)
        return None
    if not user.check_password(password):
        return None
    return user


# ------------------------------------------------------------ safe redirect

def safe_next(raw: str | None) -> str | None:
    """Validate a `?next=` target.

    Must be a path on this site. `//evil.example` is a protocol-relative URL
    that browsers treat as another origin, and is the reason this cannot just
    be a `startswith("/")` check.
    """
    if not raw:
        return None
    # "//host" is protocol-relative, and browsers also treat "/\host" that
    # way, so both must go. This is why it cannot just be startswith("/").
    if not raw.startswith("/") or raw[:2] in ("//", "/\\"):
        return None
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return None
    return raw


def login_url(cfg: Config) -> str:
    """Where to send someone who needs to log in, preserving where they were."""
    nxt = safe_next(request.full_path.rstrip("?")) if request.method == "GET" else None
    if nxt and nxt not in ("/", "/login"):
        return url_for("login", next=nxt)
    return url_for("login")
