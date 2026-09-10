"""Per-album settings that the server itself may change (DESIGN.md 5.3).

Everything here could equally live in a ``.album.toml`` beside the photos, and
for a while it did. It moved because **the photo tree is read-only to this
software**. Not merely by convention: the systemd unit sets
``ProtectHome=read-only``, and photos usually sit under a home directory, so a
web process that tried to write one would fail with EROFS on a real deployment
while working perfectly on a developer's machine — the worst way for a bug to
behave.

It lives in the state directory rather than beside ``users.toml`` because the
web process must be able to write it, and giving that process write access to
the directory holding the account list and the signing key would make a
compromise of it far more valuable than it needs to be.

**This one file in the state directory is not rebuildable.** Everything else
there is a cache that a rescan reconstructs; this is recorded human intent, and
belongs with ``users.toml`` and ``secret_key`` on the backup list.

Precedence, most specific first:

1. this file — set from the web interface or the ``cover``/``acl``/``hide``
   commands
2. the album's own ``.album.toml``, hand-written and travelling with the photos
3. the built-in default
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Config

log = logging.getLogger("harelphotos.overrides")

# The root album's key. TOML permits "" as a key, but a file people may read
# and edit is better off with something visible in it.
ROOT_KEY = "."

HEADER = """\
# Album settings written by harelphotos itself -- from the web interface, or
# from `harelphotos cover` and `harelphotos acl`.
#
# Keys are album paths relative to photo_root; "." is the whole collection.
# These take precedence over the same settings in a .album.toml beside the
# photos, which stays available for anything you prefer to hand-write.
#
# BACK THIS FILE UP. The rest of this directory is a cache that a rescan
# rebuilds; this is not.
"""


class OverrideError(Exception):
    pass


@dataclass(frozen=True)
class Override:
    cover: str | None = None
    allow: tuple[str, ...] | None = None
    allow_replace: bool | None = None
    hidden: bool | None = None

    @property
    def empty(self) -> bool:
        return (self.cover is None and self.allow is None
                and self.allow_replace is None and self.hidden is None)


def path_for(cfg: Config) -> Path:
    return cfg.overrides_file


def key_for(relpath: str) -> str:
    """The table key for an album path. The root is "." rather than ""."""
    return relpath.strip("/") or ROOT_KEY


# Parsed contents, keyed by path, with the stat that produced them. The album
# page consults this on every request, so re-parsing each time would be a waste
# -- but it must also notice a pick made a moment ago by another worker, so the
# guard is the file's own mtime and size rather than a timer.
_cache: dict[str, tuple[tuple[int, int], dict[str, "Override"]]] = {}


def load(cfg: Config) -> dict[str, Override]:
    """Read the file. A damaged one is reported and ignored, never fatal.

    Deliberately not fail-closed the way a corrupt ACL *chain* is: this is read
    on every request, and a syntax error here should not take the whole site
    down. The restrictions it carries are re-derived into the index at scan
    time, and that copy is what actually guards a request.
    """
    p = path_for(cfg)
    try:
        st = p.stat()
    except OSError:
        _cache.pop(str(p), None)
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(str(p))
    if hit is not None and hit[0] == stamp:
        return hit[1]

    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("ignoring %s: %s", p, e)
        _cache[str(p)] = (stamp, {})
        return {}

    out: dict[str, Override] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            log.warning("%s: [%s] is not a table, ignored", p, key)
            continue
        allow = value.get("allow")
        if allow is not None:
            if not isinstance(allow, list) or not all(isinstance(a, str) for a in allow):
                log.warning("%s: [%s] allow must be a list of strings, ignored", p, key)
                allow = None
            else:
                allow = tuple(allow)
        cover = value.get("cover")
        if cover is not None and not isinstance(cover, str):
            log.warning("%s: [%s] cover must be a string, ignored", p, key)
            cover = None
        rep = value.get("allow_replace")
        if rep is not None and not isinstance(rep, bool):
            log.warning("%s: [%s] allow_replace must be true or false, ignored", p, key)
            rep = None
        hid = value.get("hidden")
        if hid is not None and not isinstance(hid, bool):
            log.warning("%s: [%s] hidden must be true or false, ignored", p, key)
            hid = None
        out[key_for(key)] = Override(cover=cover, allow=allow,
                                     allow_replace=rep, hidden=hid)
    _cache[str(p)] = (stamp, out)
    return out


def get(cfg: Config, relpath: str) -> Override:
    return load(cfg).get(key_for(relpath), Override())


def _dump(table: dict[str, Override]) -> str:
    lines = [HEADER]
    for key in sorted(table):
        o = table[key]
        if o.empty:
            continue
        lines.append(f'\n[{_toml_key(key)}]')
        if o.cover is not None:
            lines.append(f"cover = {_toml_str(o.cover)}")
        if o.allow_replace:
            lines.append("allow_replace = true")
        if o.allow is not None:
            lines.append("allow = [" + ", ".join(_toml_str(a) for a in o.allow) + "]")
        if o.hidden is not None:
            lines.append(f"hidden = {'true' if o.hidden else 'false'}")
    return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_key(value: str) -> str:
    return _toml_str(value)


def set_for(cfg: Config, relpath: str, **fields) -> Path:
    """Change one album's entry, leaving every other entry alone.

    Written whole and renamed into place, so a reader never sees half a file
    and a crash mid-write cannot leave the settings truncated.
    """
    table = load(cfg)
    key = key_for(relpath)
    current = table.get(key, Override())
    merged = Override(
        cover=fields.get("cover", current.cover),
        allow=fields.get("allow", current.allow),
        allow_replace=fields.get("allow_replace", current.allow_replace),
        hidden=fields.get("hidden", current.hidden),
    )
    if merged.empty:
        table.pop(key, None)
    else:
        table[key] = merged

    p = path_for(cfg)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(_dump(table), encoding="utf-8")
        tmp.replace(p)
        _cache.pop(str(p), None)
    except OSError as e:
        raise OverrideError(f"cannot write {p}: {e}") from e
    return p


def apply(conf, over: Override):
    """Overlay an override on an album's own `.album.toml` settings."""
    from dataclasses import replace

    changes = {}
    if over.cover is not None:
        changes["cover"] = over.cover
    if over.allow is not None:
        changes["allow"] = over.allow
    if over.allow_replace is not None:
        changes["allow_replace"] = over.allow_replace
    if over.hidden is not None:
        changes["hidden"] = over.hidden
    return replace(conf, **changes) if changes else conf
