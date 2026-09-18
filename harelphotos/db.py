"""The index database.

A pure cache: every row here is derived from the filesystem and can be thrown
away, which is why there is no elaborate migration machinery.

There is a little, though, and the reason is worth stating: rebuilding the
index sets every `deriv_key` to NULL, and the next scan therefore re-encodes
the entire collection. On the machine this was written for that is days of
work to add a column. So a change that only *adds* to the schema gets a
migration; anything that changes the meaning of existing rows still ends with
"delete it and rescan", which is honest for a cache.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE dirs (
  id            INTEGER PRIMARY KEY,
  parent_id     INTEGER REFERENCES dirs(id) ON DELETE CASCADE,
  path          TEXT NOT NULL UNIQUE,       -- relative to photo_root; '' for root
  name          TEXT NOT NULL,
  title         TEXT,
  description   TEXT,
  sort          TEXT,                       -- photo order within this dir
  dirsort       TEXT,                       -- child order
  order_json    TEXT,                       -- explicit `order` list, JSON
  sort_key      TEXT,                       -- key within the parent; NULL = use name
  natkey        TEXT,                       -- natural-sort key of sort_key or name
  group_by      TEXT,
  location      TEXT,
  hidden        INTEGER NOT NULL DEFAULT 0,
  acl_chain     TEXT NOT NULL DEFAULT '[]',
  cover_spec    TEXT,                       -- the raw `cover` from .album.toml
  cover_photo   INTEGER,                    -- resolved photo id
  cfg_mtime     INTEGER,
  cfg_size      INTEGER,
  cfg_error     TEXT,
  n_photos      INTEGER NOT NULL DEFAULT 0,
  n_photos_rec  INTEGER NOT NULL DEFAULT 0,
  n_subdirs     INTEGER NOT NULL DEFAULT 0,
  links_json    TEXT,                       -- `[links]` from .album.toml, JSON
  -- Photographs reachable through this directory's links. Display only: it is
  -- never summed into an ancestor, because a link and the album it points at
  -- usually share one, and that ancestor would then count them twice.
  n_photos_linked INTEGER NOT NULL DEFAULT 0,
  -- Links anywhere beneath, counted recursively the way n_photos_rec counts
  -- photographs. A directory whose only content is subdirectories of links has
  -- no photographs of its own and none linked either, and without this it is
  -- not listed as an album at all.
  n_links_rec   INTEGER NOT NULL DEFAULT 0,
  date_min      INTEGER,
  date_max      INTEGER,
  seen          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX dirs_parent ON dirs(parent_id, natkey);
CREATE INDEX dirs_seen   ON dirs(seen);

CREATE TABLE photos (
  id          INTEGER PRIMARY KEY,
  dir_id      INTEGER NOT NULL REFERENCES dirs(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  size        INTEGER NOT NULL,        -- \\  cheap "should we look?" check
  mtime_ns    INTEGER NOT NULL,        -- /   (scan phase 1)
  content_sig BLOB,                    -- "did it really change?" (phase 2)
  hdr_stale   INTEGER NOT NULL DEFAULT 1,  -- phase 1 says "look at this one"
  width       INTEGER,
  height      INTEGER,
  taken       INTEGER,                 -- EXIF DateTimeOriginal, unix epoch
  title       TEXT,
  hidden      INTEGER NOT NULL DEFAULT 0,
  color       TEXT,                    -- '#rrggbb' placeholder
  exif_json   TEXT,
  place       TEXT,
  place_dist  INTEGER,
  landmark    TEXT,
  deriv_key   TEXT,
  deriv_tiers TEXT,                    -- JSON list of tiers actually on disk
  deriv_error TEXT,
  seen        INTEGER NOT NULL DEFAULT 0,
  UNIQUE(dir_id, name)
);
CREATE INDEX photos_dir  ON photos(dir_id, name);
CREATE INDEX photos_date ON photos(taken);
CREATE INDEX photos_seen  ON photos(seen);
CREATE INDEX photos_stale ON photos(hdr_stale) WHERE hdr_stale = 1;

-- Unused. Login throttling moved to its own auth.sqlite: it was the only
-- thing the web process wrote, and SQLite permits one writer at a time, so in
-- here it collided with a running scan and logging in failed with "database is
-- locked". Left in place only because removing it would mean a schema bump and
-- a full re-index; it should go the next time the version changes anyway.
CREATE TABLE login_attempts (
  key       TEXT PRIMARY KEY,
  failures  INTEGER NOT NULL DEFAULT 0,
  last_try  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class NotWritable(Exception):
    """The index exists but this user cannot write it."""


class SchemaMismatch(Exception):
    """The database on disk was written by a different version of the schema."""


def connect(path: Path, *, create: bool = False, read_only: bool = False) -> sqlite3.Connection:
    """Open the index, applying the pragmas the design calls for.

    WAL matters for more than speed: it lets the web application keep reading
    while a scan writes, which is what stops a scan from taking the site down.
    """
    if not create and not path.exists():
        raise FileNotFoundError(f"index database not found: {path} (run 'harelphotos scan')")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)

    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        # Checked up front because SQLite's own message for this is "attempt
        # to write a readonly database", which sends you looking for a
        # read-only *connection* when the truth is a file this user cannot
        # write -- typically because 'init' was run under sudo.
        _require_writable(path)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _require_writable(path: Path) -> None:
    """Fail early, and say what to do, when the index cannot be written.

    SQLite needs to write the directory too, not just the file: WAL puts
    ``-wal`` and ``-shm`` beside the database.
    """
    import os

    for target, what in ((path, "index"), (path.parent, "directory holding it")):
        if not target.exists():
            continue
        if not os.access(target, os.W_OK):
            raise NotWritable(
                f"cannot write {target} (the {what}): it is owned by "
                f"{_owner(target)} and this is running as {_me()}.\n"
                f"If 'harelphotos init' was run under sudo, fix it with:\n"
                f"    sudo chown -R $USER {path.parent}"
            )


def _owner(path: Path) -> str:
    try:
        import pwd

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except Exception:
        return "another user"


def _me() -> str:
    """getlogin() raises with no controlling terminal, which is exactly the
    case here -- under systemd or cron -- so never rely on it alone."""
    import os

    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return f"uid {os.getuid()}"


def initialise(conn: sqlite3.Connection) -> None:
    """Create the schema in an empty database."""
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
    )
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int | None:
    """Return the stored schema version, or None for a fresh/foreign database."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


# Migrations that only *add* to the schema, so every existing row -- and every
# `deriv_key` on it -- survives. Keyed by the version they upgrade from.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        "ALTER TABLE dirs ADD COLUMN links_json TEXT",
        "ALTER TABLE dirs ADD COLUMN n_photos_linked INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE dirs ADD COLUMN n_links_rec INTEGER NOT NULL DEFAULT 0",
    ),
}


def open_index(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open an existing index, migrating it forward where that is possible.

    A read-only caller cannot migrate, so it asks for a writable command to be
    run rather than doing it -- which also means the web process never writes
    to the index, the property that keeps a running scan from breaking logins.
    """
    conn = connect(path, read_only=read_only)
    found = schema_version(conn)
    if found == SCHEMA_VERSION:
        return conn

    try:
        version = int(found)
    except (TypeError, ValueError):
        version = -1

    if not read_only and version in MIGRATIONS:
        while version in MIGRATIONS:
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            version += 1
            set_meta(conn, "schema_version", str(version))
        conn.commit()
        if version == SCHEMA_VERSION:
            return conn

    conn.close()
    if version in MIGRATIONS or version == SCHEMA_VERSION - len(MIGRATIONS):
        raise SchemaMismatch(
            f"{path} has schema version {found!r}, expected {SCHEMA_VERSION}, and "
            f"can be upgraded in place. Run 'harelphotos scan' (or any command "
            f"that writes) once, then start the server."
        )
    raise SchemaMismatch(
        f"{path} has schema version {found!r}, expected {SCHEMA_VERSION}. "
        f"The index is a rebuildable cache: delete it and run 'harelphotos scan'. "
        f"Note that rebuilding re-encodes every photograph."
    )


def create_index(path: Path) -> sqlite3.Connection:
    """Create a new, empty index database."""
    conn = connect(path, create=True)
    initialise(conn)
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
