"""Index schema."""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import sqlite3

import pytest

from harelphotos import db


def test_create_and_reopen(tmp_path):
    path = tmp_path / "index.sqlite"
    conn = db.create_index(path)
    conn.close()
    conn = db.open_index(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()


def test_open_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        db.open_index(tmp_path / "nope.sqlite")


def test_schema_mismatch_tells_you_to_delete_it(tmp_path):
    path = tmp_path / "index.sqlite"
    conn = db.create_index(path)
    db.set_meta(conn, "schema_version", "999")
    conn.commit()
    conn.close()
    with pytest.raises(db.SchemaMismatch, match="delete it"):
        db.open_index(path)


def test_foreign_database_is_a_mismatch_not_a_crash(tmp_path):
    path = tmp_path / "index.sqlite"
    sqlite3.connect(path).execute("CREATE TABLE unrelated (x)")
    with pytest.raises(db.SchemaMismatch):
        db.open_index(path)


def test_wal_mode_is_on(tmp_path):
    # Not just for speed: it lets the web app read while a scan writes.
    conn = db.create_index(tmp_path / "index.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_deleting_a_dir_cascades_to_its_photos(tmp_path):
    conn = db.create_index(tmp_path / "index.sqlite")
    conn.execute("INSERT INTO dirs (id, path, name, seen) VALUES (1, '', 'root', 1)")
    conn.execute(
        "INSERT INTO photos (dir_id, name, size, mtime_ns, seen) VALUES (1, 'a.jpg', 1, 1, 1)"
    )
    conn.commit()
    conn.execute("DELETE FROM dirs WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 0
    conn.close()


def test_photo_names_are_unique_per_directory(tmp_path):
    conn = db.create_index(tmp_path / "index.sqlite")
    conn.execute("INSERT INTO dirs (id, path, name, seen) VALUES (1, '', 'root', 1)")
    conn.execute(
        "INSERT INTO photos (dir_id, name, size, mtime_ns, seen) VALUES (1, 'a.jpg', 1, 1, 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO photos (dir_id, name, size, mtime_ns, seen) "
            "VALUES (1, 'a.jpg', 2, 2, 1)"
        )
    conn.close()


def test_dir_paths_are_unique(tmp_path):
    conn = db.create_index(tmp_path / "index.sqlite")
    conn.execute("INSERT INTO dirs (path, name, seen) VALUES ('2019', '2019', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dirs (path, name, seen) VALUES ('2019', '2019', 1)")
    conn.close()


def test_meta_round_trip(tmp_path):
    conn = db.create_index(tmp_path / "index.sqlite")
    assert db.get_meta(conn, "nothing") is None
    db.set_meta(conn, "k", "v1")
    db.set_meta(conn, "k", "v2")          # upsert, not a duplicate row
    assert db.get_meta(conn, "k") == "v2"
    conn.close()


def test_read_only_connection_cannot_write(tmp_path):
    path = tmp_path / "index.sqlite"
    db.create_index(path).close()
    conn = db.open_index(path, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO dirs (path, name, seen) VALUES ('x', 'x', 1)")
    conn.close()


def test_expected_columns_exist(tmp_path):
    # Guards against the schema drifting away from what later milestones expect.
    conn = db.create_index(tmp_path / "index.sqlite")
    dirs = {r["name"] for r in conn.execute("PRAGMA table_info(dirs)")}
    photos = {r["name"] for r in conn.execute("PRAGMA table_info(photos)")}
    assert {"acl_chain", "natkey", "sort_key", "order_json", "cfg_error", "location"} <= dirs
    assert {"content_sig", "deriv_key", "taken", "place", "landmark", "color"} <= photos
    conn.close()


def test_a_root_owned_index_says_what_to_do(tmp_path, monkeypatch):
    """SQLite's own message here is "attempt to write a readonly database",
    which sends you looking for a read-only connection rather than at the file
    permissions -- and running 'init' under sudo is an easy way to get here."""
    import os

    path = tmp_path / "index.sqlite"
    db.create_index(path).close()
    real_access = os.access
    monkeypatch.setattr(
        os, "access", lambda p, m: False if str(p) == str(path) else real_access(p, m)
    )
    with pytest.raises(db.NotWritable, match="chown"):
        db.connect(path)


def test_an_unwritable_directory_is_caught_too(tmp_path, monkeypatch):
    """WAL writes -wal and -shm beside the database, so the directory must be
    writable even when the database file itself is."""
    import os

    path = tmp_path / "index.sqlite"
    db.create_index(path).close()
    real_access = os.access
    monkeypatch.setattr(
        os, "access",
        lambda p, m: False if str(p) == str(tmp_path) else real_access(p, m),
    )
    with pytest.raises(db.NotWritable, match="directory holding it"):
        db.connect(path)


def test_read_only_opens_are_unaffected(tmp_path, monkeypatch):
    """Serving from an index you cannot write is entirely normal."""
    import os

    path = tmp_path / "index.sqlite"
    db.create_index(path).close()
    monkeypatch.setattr(os, "access", lambda p, m: False)
    db.open_index(path, read_only=True).close()


def test_an_older_index_is_upgraded_in_place_and_keeps_its_work(tmp_path):
    """Rebuilding the index is not a free operation.

    Every `deriv_key` goes with it, so the next scan re-encodes the whole
    collection -- days of work on a weak machine to add a column. A migration
    that only *adds* to the schema must therefore leave every existing row
    alone, and this asserts the one that matters survives.
    """
    import re
    import sqlite3

    from harelphotos import db as db_mod

    path = tmp_path / "index.sqlite"
    # Version 1's schema is this one minus whatever the migration to 2 adds, so
    # that adding a column to both does not leave this test comparing today's
    # schema with itself.
    added = {re.search(r"ADD COLUMN (\w+)", sql).group(1)
             for sql in db_mod.MIGRATIONS[1]}
    kept, pending = [], []
    for line in db_mod.SCHEMA.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("--"):
            pending.append(line)                       # may belong to a new column
            continue
        if stripped.split(" ")[0] in added:
            pending.clear()                            # and so did its comment
            continue
        kept.extend(pending); pending.clear(); kept.append(line)
    old = "".join(kept + pending)
    assert added and not (added & {c for c in re.findall(r"^\s*(\w+)\s+\w", old, re.M)})
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO dirs (path, name, natkey) VALUES ('2019', '2019', '2019')")
    conn.execute("INSERT INTO photos (dir_id, name, size, mtime_ns, deriv_key) "
                 "VALUES (1, 'a.jpg', 10, 0, 'expensive')")
    conn.commit()
    conn.close()

    conn = db_mod.open_index(path)                     # writable: migrates
    assert db_mod.schema_version(conn) == db_mod.SCHEMA_VERSION
    assert conn.execute("SELECT deriv_key FROM photos").fetchone()[0] == "expensive"
    columns = {r[1] for r in conn.execute("PRAGMA table_info(dirs)")}
    assert added <= columns
    conn.close()


def test_a_read_only_open_of_an_older_index_asks_for_a_scan_rather_than_a_delete(tmp_path):
    """The web process must never write to the index -- that read-only
    arrangement is what keeps a running scan from breaking logins. So it cannot
    migrate, and the message must not tell anyone to delete the file, which
    would cost them the re-encode this migration exists to avoid.
    """
    import re
    import sqlite3

    import pytest

    from harelphotos import db as db_mod

    path = tmp_path / "index.sqlite"
    old = db_mod.SCHEMA
    old = old.replace(
        "  links_json    TEXT,                       -- `[links]` from .album.toml, JSON\n", "")
    old = re.sub(r"  -- Photographs reachable.*?n_photos_linked INTEGER NOT NULL DEFAULT 0,\n",
                 "", old, flags=re.S)
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    conn.commit()
    conn.close()

    with pytest.raises(db_mod.SchemaMismatch) as e:
        db_mod.open_index(path, read_only=True)
    assert "scan" in str(e.value)
    assert "delete" not in str(e.value).lower()


def test_the_migration_does_not_cost_a_single_re_encode(tmp_path, capfd):
    """The whole reason the migration exists, asserted end to end.

    Re-encoding this collection is over a week of work on the machine it runs
    on, so "adding a column is free" cannot be left as an argument about which
    code paths do what. This builds a version 1 index with real derived images
    beside it, runs the actual `harelphotos scan` command with no arguments,
    and requires that afterwards every derived file on disk is byte for byte
    the file that was there before -- down to its modification time, so that a
    re-encode producing identical output would still be caught.
    """
    import hashlib
    import sqlite3

    from harelphotos import cli, db as db_mod

    from . import fixtures

    photos = tmp_path / "pictures"
    for name in ("a.jpg", "b.jpg"):
        fixtures.make_jpeg(photos / "2019" / name, size=(900, 600))
    fixtures.make_jpeg(photos / "2019" / "sub" / "c.jpg", size=(900, 600))
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "secret_key").write_bytes(b"0" * 32)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'photo_root = "{photos}"\n'
        f'derived_root = "{state / "derived"}"\n'
        f'index_db = "{state / "index.sqlite"}"\n'
        f'users_file = "{tmp_path / "users.toml"}"\n'
        f'secret_key_file = "{tmp_path / "secret_key"}"\n'
        f'[scan]\njobs = 1\n'
        f'[encode]\nspeed = 10\n', encoding="utf-8")
    db_mod.create_index(state / "index.sqlite").close()      # what `init` does

    assert cli.main(["-q", "-c", str(cfg_path), "scan"]) == 0

    def snapshot():
        out = {}
        for p in sorted((state / "derived").rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p.relative_to(state))] = (
                    st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        return out

    before = snapshot()
    assert len(before) >= 3, "the fixture must actually have produced derivatives"

    index = state / "index.sqlite"
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    keys_before = {r["id"]: r["deriv_key"]
                   for r in conn.execute("SELECT id, deriv_key FROM photos")}
    assert all(keys_before.values()), "every photo should have been derived"

    # Put the index back to version 1, the state every existing index is in.
    import re
    for sql in db_mod.MIGRATIONS[1]:
        conn.execute(f"ALTER TABLE dirs DROP COLUMN {re.search(r'ADD COLUMN (\w+)', sql).group(1)}")
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    # Exactly what the upgrade instructions say to run. Not --full, which is
    # the one documented way to ask for the re-encode this test forbids.
    capfd.readouterr()
    assert cli.main(["-q", "-c", str(cfg_path), "scan"]) == 0

    # And it says so, rather than upgrading silently inside whichever command
    # happened to open the index first.
    said = capfd.readouterr().err
    assert "schema version 1 to 2" in said
    assert "no derived image was regenerated" in said.replace("\n", " ")

    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    assert db_mod.schema_version(conn) == db_mod.SCHEMA_VERSION      # it migrated
    keys_after = {r["id"]: r["deriv_key"]
                  for r in conn.execute("SELECT id, deriv_key FROM photos")}
    conn.close()

    assert keys_after == keys_before
    assert snapshot() == before


def test_an_interrupted_migration_leaves_nothing_half_done(tmp_path):
    """Killed halfway, the upgrade must roll back rather than leave a database
    that says version 1 and already has the new columns: the next attempt would
    fail on "duplicate column name", and the obvious way out of that is to
    delete an index that costs a week of encoding to rebuild.
    """
    import sqlite3

    from harelphotos import db as db_mod

    path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE dirs (id INTEGER PRIMARY KEY);"
        "CREATE TABLE photos (id INTEGER PRIMARY KEY, deriv_key TEXT);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO photos (deriv_key) VALUES ('expensive')")
    conn.commit()
    conn.close()

    # The last statement of the upgrade fails, standing in for the process
    # being killed at the worst possible moment.
    doomed = dict(db_mod.MIGRATIONS)
    doomed[1] = db_mod.MIGRATIONS[1] + ("ALTER TABLE nonexistent ADD COLUMN x",)
    real = db_mod.MIGRATIONS
    db_mod.MIGRATIONS = doomed
    try:
        with pytest.raises(sqlite3.OperationalError):
            db_mod.open_index(path)
    finally:
        db_mod.MIGRATIONS = real

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert db_mod.schema_version(conn) == 1                  # still version 1
    assert [r["name"] for r in conn.execute("PRAGMA table_info(dirs)")] == ["id"]
    assert conn.execute("SELECT deriv_key FROM photos").fetchone()[0] == "expensive"
    conn.close()

    # ...and so the real upgrade still works afterwards, rather than being
    # stuck on a column that is already there.
    conn = db_mod.open_index(path)
    assert db_mod.schema_version(conn) == db_mod.SCHEMA_VERSION
    assert conn.execute("SELECT deriv_key FROM photos").fetchone()[0] == "expensive"
    conn.close()
