"""Accounts, stored in a hand-editable users.toml (DESIGN.md 5.2).

Not a database, deliberately: the PLAN asks for metadata in files, and there
are going to be about eight of these. ``harelphotos user ...`` maintains the
file, but editing it by hand is a supported workflow, so the writer preserves
nothing clever and the reader tolerates whatever a human plausibly types.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


class UsersError(Exception):
    """users.toml is missing or malformed. Fatal: nobody could log in anyway."""


@dataclass(frozen=True)
class User:
    """One account.

    ``token`` is the table key, and is the identity that appears in ACLs and in
    the session cookie. For local accounts it is a username; for Google-only
    accounts the convention is to use the email as the key.
    """

    token: str
    name: str
    password_hash: str | None = None
    google: str | None = None
    admin: bool = False
    epoch: int = 1

    @property
    def can_login_locally(self) -> bool:
        return self.password_hash is not None

    def check_password(self, password: str) -> bool:
        if self.password_hash is None:
            return False
        return check_password_hash(self.password_hash, password)


@dataclass(frozen=True)
class Users:
    by_token: dict[str, User]

    def get(self, token: str) -> User | None:
        return self.by_token.get(token)

    def by_google_email(self, email: str) -> User | None:
        """Find the account a verified Google email maps to.

        Matches an explicit ``google = "..."`` field first, then falls back to
        the table key itself, which is how a pure-Google account is written.
        No match means refuse: Google asserts identity, the allowlist grants
        access (DESIGN.md 12.2).
        """
        needle = email.strip().casefold()
        for u in self.by_token.values():
            if u.google and u.google.strip().casefold() == needle:
                return u
        for u in self.by_token.values():
            if u.token.strip().casefold() == needle:
                return u
        return None

    def __len__(self) -> int:
        return len(self.by_token)

    def __iter__(self):
        return iter(self.by_token.values())


def parse(text: str, source: str = "users.toml") -> Users:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise UsersError(f"{source}: invalid TOML: {e}") from e

    table = raw.get("users")
    if table is None:
        return Users(by_token={})
    if not isinstance(table, dict):
        raise UsersError(f"{source}: [users] must be a table of per-user tables")

    out: dict[str, User] = {}
    for token, meta in table.items():
        if not isinstance(meta, dict):
            raise UsersError(f"{source}: [users.{token}] must be a table")
        pw = meta.get("password")
        google = meta.get("google")
        if pw is not None and not isinstance(pw, str):
            raise UsersError(f"{source}: [users.{token}] password must be a string")
        if google is not None and not isinstance(google, str):
            raise UsersError(f"{source}: [users.{token}] google must be a string")
        if pw is None and google is None:
            raise UsersError(
                f"{source}: [users.{token}] has neither 'password' nor 'google', "
                f"so it can never be used to log in"
            )
        out[token] = User(
            token=token,
            name=str(meta.get("name", token)),
            password_hash=pw,
            google=google,
            admin=bool(meta.get("admin", False)),
            epoch=int(meta.get("epoch", 1)),
        )
    return Users(by_token=out)


def load(path: Path) -> Users:
    try:
        return parse(path.read_text(encoding="utf-8"), str(path))
    except FileNotFoundError:
        # An absent file is an empty allowlist, not an error: it is the state
        # right after 'init' and before the first 'user add'.
        return Users(by_token={})
    except OSError as e:
        raise UsersError(f"{path}: cannot read: {e}") from e


def _quote_key(token: str) -> str:
    """TOML bare keys allow only [A-Za-z0-9_-]; emails need quoting."""
    if token and all(c.isalnum() or c in "_-" for c in token):
        return token
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _quote_value(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dumps(users: Users) -> str:
    """Render users.toml. Stable ordering so diffs stay readable."""
    lines = [
        "# harelphotos accounts. Editable by hand; see DESIGN.md 5.2.",
        "# The table key is the identity used in .album.toml 'allow' lists.",
        "",
    ]
    for token in sorted(users.by_token):
        u = users.by_token[token]
        lines.append(f"[users.{_quote_key(token)}]")
        lines.append(f"name = {_quote_value(u.name)}")
        if u.password_hash:
            lines.append(f"password = {_quote_value(u.password_hash)}")
        if u.google:
            lines.append(f"google = {_quote_value(u.google)}")
        if u.admin:
            lines.append("admin = true")
        if u.epoch != 1:
            lines.append(f"epoch = {u.epoch}")
        lines.append("")
    return "\n".join(lines)


def save(path: Path, users: Users) -> None:
    """Write users.toml atomically, mode 0600 — it holds password hashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(dumps(users), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def hash_password(password: str) -> str:
    """Werkzeug's default (scrypt), so no crypto dependency of our own."""
    return generate_password_hash(password)


# A hash of a fixed dummy password, used to spend the same CPU on an unknown
# username as on a known one so response time does not reveal which accounts
# exist (DESIGN.md 12.1). Computed lazily to keep import cheap.
_DUMMY_HASH: str | None = None


def waste_time_like_a_real_check(password: str) -> bool:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = generate_password_hash("not-a-real-password")
    check_password_hash(_DUMMY_HASH, password)
    return False
