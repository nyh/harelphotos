"""Access control (DESIGN.md 6).

The rule is that **restrictions accumulate**: a user may view a directory if,
for *every* ``allow`` list on the path from the root down to it, the user
matches that list. Marking one directory private therefore locks its whole
subtree, and nothing deeper can accidentally re-open it.

``allow_replace = true`` is the explicit escape hatch: it discards the
ancestors' lists so that one album inside a private tree can be shared more
widely.

Two deliberate splits in responsibility:

* The **chain** of allow-lists is computed at scan time and stored per
  directory, so a request checks one row rather than walking the tree.
* **Group expansion happens at request time**, so editing a group in
  config.toml takes effect immediately without a rescan.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence

# A chain is a list of allow-lists, outermost first. Empty means unrestricted.
Chain = tuple[tuple[str, ...], ...]

GROUP_PREFIX = "@"


def extend_chain(parent: Chain, allow: Sequence[str] | None, allow_replace: bool) -> Chain:
    """Compute a directory's chain from its parent's chain and its own config."""
    base: Chain = () if allow_replace else tuple(parent)
    if allow is None:
        return base
    return base + (tuple(allow),)


def dumps(chain: Chain) -> str:
    """Serialise a chain for the ``dirs.acl_chain`` column."""
    return json.dumps([list(link) for link in chain], separators=(",", ":"))


def loads(text: str | None) -> Chain:
    """Parse a stored chain. A corrupt value is treated as *restricted*.

    Failing closed matters here: if we cannot tell what the restriction was,
    the safe reading is 'nobody', not 'everybody'.
    """
    if not text:
        return ()
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return ((),)  # an empty allow-list matches no one
    if not isinstance(raw, list):
        return ((),)
    out: list[tuple[str, ...]] = []
    for link in raw:
        if not isinstance(link, list) or not all(isinstance(x, str) for x in link):
            return ((),)
        out.append(tuple(link))
    return tuple(out)


def expand_group(
    name: str,
    groups: Mapping[str, Sequence[str]],
    _seen: frozenset[str] = frozenset(),
) -> set[str]:
    """Expand a group name to a set of user tokens, recursively.

    Groups may reference other groups. ``_seen`` guards against a cycle, which
    would otherwise be an easy way to hang every request by editing a config
    file.
    """
    if name in _seen:
        return set()
    members = groups.get(name)
    if members is None:
        return set()
    out: set[str] = set()
    for m in members:
        if m.startswith(GROUP_PREFIX):
            out |= expand_group(m[1:], groups, _seen | {name})
        else:
            out.add(m)
    return out


def matches_link(user: str, link: Iterable[str], groups: Mapping[str, Sequence[str]]) -> bool:
    """Does one allow-list admit this user?"""
    for token in link:
        if token.startswith(GROUP_PREFIX):
            if user in expand_group(token[1:], groups):
                return True
        elif token == user:
            return True
    return False


def can_view(
    chain: Chain,
    user: str | None,
    groups: Mapping[str, Sequence[str]],
    is_admin: bool = False,
) -> bool:
    """Apply the accumulate rule.

    ``user`` of None is an anonymous request, which never passes a restriction —
    and the caller should have rejected it before reaching here anyway (the
    login gate in DESIGN.md 12.3).
    """
    if is_admin:
        return True
    if not chain:
        return True
    if user is None:
        return False
    return all(matches_link(user, link, groups) for link in chain)
