"""The index database (DESIGN.md 7).

A pure cache: every row here is derived from the filesystem and can be thrown
away. If the schema version does not match and there is no migration path, the
right answer is to delete the file and rescan — which is why there is no
elaborate migration machinery.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

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
  cover_photo   INTEGER,
  cfg_mtime     INTEGER,
  cfg_size      INTEGER,
  cfg_error     TEXT,
  n_photos      INTEGER NOT NULL DEFAULT 0,
  n_photos_rec  INTEGER NOT NULL DEFAULT 0,
  n_subdirs     INTEGER NOT NULL DEFAULT 0,
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
CREATE INDEX photos_seen ON photos(seen);

-- Login throttling (DESIGN.md 12.1). It lives here rather than in users.toml
-- because it is transient state, not configuration; losing it on a rebuild
-- just resets the backoff, which is harmless.
CREATE TABLE login_attempts (
  key       TEXT PRIMARY KEY,          -- "ip|username"
  failures  INTEGER NOT NULL DEFAULT 0,
  last_try  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


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
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


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


def open_index(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open an existing index and verify its schema version."""
    conn = connect(path, read_only=read_only)
    found = schema_version(conn)
    if found != SCHEMA_VERSION:
        conn.close()
        raise SchemaMismatch(
            f"{path} has schema version {found!r}, expected {SCHEMA_VERSION}. "
            f"The index is a rebuildable cache: delete it and run 'harelphotos scan'."
        )
    return conn


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
