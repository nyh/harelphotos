"""``harelphotos acl`` — inspect and set who may see a directory (DESIGN.md 6).

Restrictions can be written by hand in a ``.album.toml`` beside the photos, and
that keeps working. What this command *writes* goes to the overrides file in
the state directory instead (see ``overrides.py``), because the photo tree is
read-only to this software.

It exists because the interesting question is not "what does this file say" but
"who can actually see this album, and why" — and that answer is assembled from
every ``.album.toml`` between the root and here, each one possibly overridden,
plus the group definitions in the global config.

Two mistakes it is built to catch, both of which fail *silently* and in the
dangerous direction:

* A restriction that was never applied, because the index still holds the chain
  computed at the last scan. Editing the file changes nothing until a rescan,
  so the command rescans the subtree itself and says so.
* A name that matches nobody — a typo, or a relative who was later removed.
  The album then quietly shows to fewer people than intended, which nobody
  notices, because nobody complains about photos they cannot see.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import acl, album, overrides, users as users_mod
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

    Read from the configuration -- the ``.album.toml`` files and the overrides
    on top of them -- rather than from the stored chain, because the point is
    to show what is configured *now*, including edits made since the last scan
    that are not yet in force.
    """
    parts = [p for p in relpath.split("/") if p]
    over = overrides.load(cfg)
    links: list[Link] = []
    here = ""
    for i in range(len(parts) + 1):
        d = cfg.photo_root / here if here else cfg.photo_root
        conf = album.load(d)
        entry = over.get(overrides.key_for(here))
        if entry:
            conf = overrides.apply(conf, entry)
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

def write_allow(
    cfg: Config,
    relpath: str,
    allow: list[str] | None,
    *,
    replace: bool = False,
) -> Path:
    """Set or clear the restriction on one directory.

    Written to the overrides file in the state directory, never to a
    ``.album.toml`` in the photo tree: that tree is read-only to this software,
    and on a real deployment the systemd unit makes it literally so.

    A hand-written ``allow`` beside the photos keeps working and keeps
    travelling with them; an entry here simply takes precedence over it. Which
    is also how you undo one: `--clear` removes the override and whatever the
    `.album.toml` says applies again.
    """
    d = cfg.photo_root / relpath if relpath else cfg.photo_root
    if not d.is_dir():
        raise AclError(f"{d} is not a directory")
    try:
        return overrides.set_for(
            cfg,
            relpath,
            allow=tuple(allow) if allow is not None else None,
            allow_replace=replace if allow is not None else None,
        )
    except overrides.OverrideError as e:
        raise AclError(str(e)) from e


def restricted_dirs(conn) -> list[tuple[str, acl.Chain]]:
    """Every directory the index believes is restricted, from the stored chain."""
    out = []
    for r in conn.execute(
        "SELECT path, acl_chain FROM dirs WHERE acl_chain IS NOT NULL "
        "AND acl_chain NOT IN ('', '[]') ORDER BY path"
    ):
        out.append((r["path"], acl.loads(r["acl_chain"])))
    return out
