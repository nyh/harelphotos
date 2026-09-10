"""``harelphotos acl`` — inspect and set who may see a directory (DESIGN.md 6).

Restrictions live in ``.album.toml`` files, which you can perfectly well edit
by hand. This exists because the interesting question is not "what does this
file say" but "who can actually see this album, and why" — and that answer is
assembled from every ``.album.toml`` between the root and here, plus the group
definitions in the global config.

Two mistakes it is built to catch, both of which fail *silently* and in the
dangerous direction:

* A restriction that was never applied, because the index still holds the chain
  computed at the last scan. Editing the file changes nothing until a rescan,
  so the command rescans the subtree itself and says so.
* A name that matches nobody — a typo, or a relative who was later removed.
  The album then quietly shows to fewer people than intended, which nobody
  notices, because nobody complains about photos they cannot see.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import acl, album, users as users_mod
from .config import Config


class AclError(Exception):
    pass


@dataclass
class Link:
    """One allow-list in the chain, and the directory that imposed it."""

    dir_path: str
    allow: tuple[str, ...]
    replaced: bool


def chain_with_sources(cfg: Config, relpath: str) -> list[Link]:
    """Walk from the root down, recording who restricted what.

    Read from the ``.album.toml`` files rather than the stored chain, because
    the point is to show what the configuration *says*, including edits made
    since the last scan.
    """
    parts = [p for p in relpath.split("/") if p]
    links: list[Link] = []
    here = ""
    for i in range(len(parts) + 1):
        d = cfg.photo_root / here if here else cfg.photo_root
        conf = album.load(d)
        if conf.allow_replace:
            links = []
        if conf.allow is not None:
            links.append(Link(here or ".", tuple(conf.allow), conf.allow_replace))
        if i < len(parts):
            here = f"{here}/{parts[i]}" if here else parts[i]
    return links


def who_can_view(cfg: Config, links: list[Link]) -> tuple[set[str], set[str]]:
    """The accounts admitted by every link, and the names that match nobody.

    The second half is the useful one. A token that is neither an account nor a
    group restricts the album further than anyone intended, and nothing else
    ever reports it.
    """
    known = users_mod.load(cfg.users_file)
    tokens = set(known.by_token)
    unknown: set[str] = set()
    for link in links:
        for token in link.allow:
            if token.startswith(acl.GROUP_PREFIX):
                name = token[1:]
                if name not in cfg.groups:
                    unknown.add(token)
                elif not acl.expand_group(name, cfg.groups):
                    unknown.add(token)
            elif token not in tokens:
                unknown.add(token)

    chain = tuple(link.allow for link in links)
    can = {
        t for t in tokens
        if acl.can_view(chain, t, cfg.groups, is_admin=known.by_token[t].admin)
    }
    return can, unknown


# ----------------------------------------------------------------- writing

def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _strip_key(text: str, key: str) -> str:
    """Remove a top-level ``key = ...`` assignment, including a wrapped array.

    Line-based rather than a TOML round-trip, so comments and formatting the
    owner wrote by hand survive. `write_allow` verifies the result afterwards.
    """
    out: list[str] = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith(key) and stripped[len(key):].lstrip().startswith("="):
            # Consume a multi-line array too.
            depth = lines[i].count("[") - lines[i].count("]")
            i += 1
            while depth > 0 and i < len(lines):
                depth += lines[i].count("[") - lines[i].count("]")
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def write_allow(
    cfg: Config,
    relpath: str,
    allow: list[str] | None,
    *,
    replace: bool = False,
) -> Path:
    """Set or clear the restriction on one directory's ``.album.toml``.

    Everything else in the file is preserved. Afterwards the file is re-parsed
    and every other key compared with what it held before; if anything else
    moved, the original is put back and this raises rather than leaving an
    album's settings quietly mangled.
    """
    d = cfg.photo_root / relpath if relpath else cfg.photo_root
    if not d.is_dir():
        raise AclError(f"{d} is not a directory")
    path = d / album.ALBUM_FILE

    before_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        before = tomllib.loads(before_text) if before_text else {}
    except tomllib.TOMLDecodeError as e:
        raise AclError(f"{path} is not valid TOML, so it will not be edited: {e}") from e

    body = _strip_key(_strip_key(before_text, "allow"), "allow_replace")
    if body and not body.endswith("\n"):
        body += "\n"
    if allow is not None:
        if replace:
            body += "allow_replace = true\n"
        body += "allow = [" + ", ".join(_quote(a) for a in allow) + "]\n"

    backup = path.with_suffix(path.suffix + ".bak") if path.is_file() else None
    if backup is not None:
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)

    try:
        after = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        _restore(path, backup, before_text)
        raise AclError(f"editing {path} produced invalid TOML ({e}); it was left alone") from e

    keys = (set(before) | set(after)) - {"allow", "allow_replace"}
    changed = [k for k in sorted(keys) if before.get(k) != after.get(k)]
    if changed:
        _restore(path, backup, before_text)
        raise AclError(
            f"editing {path} would have changed {', '.join(changed)}; it was left alone"
        )
    if backup is not None:
        backup.unlink(missing_ok=True)
    return path


def _restore(path: Path, backup: Path | None, original: str) -> None:
    if backup is not None and backup.is_file():
        backup.replace(path)
    elif original:
        path.write_text(original, encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def restricted_dirs(conn) -> list[tuple[str, acl.Chain]]:
    """Every directory the index believes is restricted, from the stored chain."""
    out = []
    for r in conn.execute(
        "SELECT path, acl_chain FROM dirs WHERE acl_chain IS NOT NULL "
        "AND acl_chain NOT IN ('', '[]') ORDER BY path"
    ):
        out.append((r["path"], acl.loads(r["acl_chain"])))
    return out
