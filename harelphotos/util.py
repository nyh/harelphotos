"""Small helpers shared across modules."""

from __future__ import annotations

import re

# Width that digit runs are zero-padded to when building a natural-sort key.
# Ten digits is comfortably more than any plausible number in a directory name
# and keeps keys short enough to index.
_NATKEY_DIGITS = 10

_DIGIT_RUN = re.compile(r"\d+")


def natkey(name: str) -> str:
    """Collation key that makes a plain string sort compare numerically.

    SQLite cannot natural-sort, so the scanner precomputes this once per
    directory and lets ordinary ``ORDER BY`` do the work (DESIGN.md 7).

    >>> natkey("Day 10") > natkey("Day 2")
    True
    >>> natkey("2009") < natkey("2010")
    True
    """
    return _DIGIT_RUN.sub(lambda m: m.group().zfill(_NATKEY_DIGITS), name.casefold())


def prettify_name(name: str) -> str:
    """Turn a directory name into a default title.

    Deliberately conservative: underscores become spaces and runs of
    whitespace collapse, and nothing else is touched. An explicit ``title`` in
    .album.toml always wins, so guessing harder here would only produce
    surprises.
    """
    return " ".join(name.replace("_", " ").split()) or name
