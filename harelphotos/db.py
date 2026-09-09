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
  cover_spec    TEXT,                       -- the raw `cover` from .album.toml
  cover_photo   INTEGER,                    -- resolved photo id
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
