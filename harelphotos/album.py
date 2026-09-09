"""Per-directory metadata: .album.toml (DESIGN.md 5.3).

Parse errors here are deliberately **non-fatal**. A typo in one TOML file must
never take the site down, so every problem is recorded as a message on the
returned object and the affected keys fall back to their defaults. The scanner
stores that message in ``dirs.cfg_error`` and ``harelphotos check`` lists it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ALBUM_FILE = ".album.toml"

SORT_VALUES = ("date", "exif", "mtime", "name")
DIRSORT_VALUES = ("name", "date")
GROUP_BY_VALUES = ("none", "day", "month")

# cover = "auto:first" | "auto:middle" | "auto:hash", or a photo path.
COVER_AUTO = ("auto", "auto:first", "auto:middle", "auto:hash")


@dataclass
class PhotoMeta:
    """Per-photo overrides from a [photos."NAME"] table."""

    title: str | None = None
    hidden: bool = False


@dataclass
class AlbumConfig:
    """The contents of one .album.toml, or all-defaults if there wasn't one."""

    title: str | None = None
    description: str | None = None
    cover: str = "auto"
    sort: str | None = None          # None = inherit the global default
    reverse_sort: bool = False
    dirsort: str | None = None       # None = inherit [ui] dirsort
    reverse_dirsort: bool = False
    order: tuple[str, ...] = ()
    sort_key: str | None = None
    group_by: str = "none"
    hidden: bool = False
    allow: tuple[str, ...] | None = None   # None = no restriction at this level
    allow_replace: bool = False
    location: str | None = None
    photos: dict[str, PhotoMeta] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def error_text(self) -> str | None:
        return "; ".join(self.errors) if self.errors else None


def _split_direction(value: str) -> tuple[str, bool]:
    """Strip a leading '-' meaning 'reverse'."""
    if value.startswith("-"):
        return value[1:], True
    return value, False


class _Collector:
    """Accumulates complaints instead of raising them."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def bad(self, msg: str) -> None:
        self.errors.append(msg)

    def string(self, raw: dict, key: str) -> str | None:
        v = raw.get(key)
        if v is None:
            return None
        if not isinstance(v, str):
            self.bad(f"{key!r} must be a string, got {type(v).__name__}")
            return None
        return v

    def boolean(self, raw: dict, key: str, default: bool) -> bool:
        v = raw.get(key)
        if v is None:
            return default
        if not isinstance(v, bool):
            self.bad(f"{key!r} must be true or false, got {type(v).__name__}")
            return default
        return v

    def str_list(self, raw: dict, key: str) -> tuple[str, ...] | None:
        v = raw.get(key)
        if v is None:
            return None
        if isinstance(v, str):
            # A single string where a list belongs is a common slip and
            # unambiguous, so accept it rather than dropping the whole key.
            return (v,)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            self.bad(f"{key!r} must be a list of strings")
            return None
        return tuple(v)

    def enum(self, raw: dict, key: str, allowed: tuple[str, ...], default: str) -> str:
        v = self.string(raw, key)
        if v is None:
            return default
        if v not in allowed:
            self.bad(f"{key!r} must be one of {', '.join(allowed)}, got {v!r}")
            return default
        return v


def parse(text: str) -> AlbumConfig:
    """Parse .album.toml content. Never raises."""
    c = _Collector()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return AlbumConfig(errors=(f"invalid TOML: {e}",))

    sort = c.string(raw, "sort")
    reverse_sort = False
    if sort is not None:
        sort, reverse_sort = _split_direction(sort)
        if sort not in SORT_VALUES:
            c.bad(f"'sort' must be one of {', '.join(SORT_VALUES)} (optionally '-'-prefixed)")
            sort, reverse_sort = None, False

    dirsort = c.string(raw, "dirsort")
    reverse_dirsort = False
    if dirsort is not None:
        dirsort, reverse_dirsort = _split_direction(dirsort)
        if dirsort not in DIRSORT_VALUES:
            c.bad(
                f"'dirsort' must be one of {', '.join(DIRSORT_VALUES)} "
                f"(optionally '-'-prefixed)"
            )
            dirsort, reverse_dirsort = None, False

    cover = c.string(raw, "cover") or "auto"
    if cover.startswith("auto") and cover not in COVER_AUTO:
        c.bad(f"'cover' must be a photo path or one of {', '.join(COVER_AUTO)}")
        cover = "auto"

    photos: dict[str, PhotoMeta] = {}
    photos_raw = raw.get("photos")
    if photos_raw is not None:
        if not isinstance(photos_raw, dict):
            c.bad("'photos' must be a table of per-photo tables")
        else:
            for name, meta in photos_raw.items():
                if not isinstance(meta, dict):
                    c.bad(f"[photos.{name!r}] must be a table")
                    continue
                pc = _Collector()
                photos[name] = PhotoMeta(
                    title=pc.string(meta, "title"),
                    hidden=pc.boolean(meta, "hidden", False),
                )
                for err in pc.errors:
                    c.bad(f"[photos.{name!r}]: {err}")

    return AlbumConfig(
        title=c.string(raw, "title"),
        description=c.string(raw, "description"),
        cover=cover,
        sort=sort,
        reverse_sort=reverse_sort,
        dirsort=dirsort,
        reverse_dirsort=reverse_dirsort,
        order=c.str_list(raw, "order") or (),
        sort_key=c.string(raw, "sort_key"),
        group_by=c.enum(raw, "group_by", GROUP_BY_VALUES, "none"),
        hidden=c.boolean(raw, "hidden", False),
        allow=c.str_list(raw, "allow"),
        allow_replace=c.boolean(raw, "allow_replace", False),
        location=c.string(raw, "location"),
        photos=photos,
        errors=tuple(c.errors),
    )


def load(directory: Path) -> AlbumConfig:
    """Read and parse ``directory/.album.toml``; all-defaults if absent."""
    path = directory / ALBUM_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AlbumConfig()
    except OSError as e:
        return AlbumConfig(errors=(f"cannot read {ALBUM_FILE}: {e}",))
    except UnicodeDecodeError as e:
        return AlbumConfig(errors=(f"{ALBUM_FILE} is not valid UTF-8: {e}",))
    return parse(text)
