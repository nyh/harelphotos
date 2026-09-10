"""Copying generated state to another machine (DESIGN.md, M8).

No test here talks to a real host: the parts worth testing are the ones that
would corrupt a *serving* site, and they are all local. Notably the index
snapshot, which must be consistent even though the index is a live WAL-mode
database being written to.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

import sqlite3

import pytest

from harelphotos import db, sync

from . import fixtures


def test_destination_parsing():
    d = sync.Destination.parse("nyh@harel.org.il:/var/lib/harelphotos")
    assert d.host == "nyh@harel.org.il"
    assert d.path == "/var/lib/harelphotos"
    # A trailing slash would turn into a doubled one when paths are joined.
    assert sync.Destination.parse("h:/srv/x/").path == "/srv/x"


def test_destination_rejects_nonsense():
    for bad, why in (
        ("/just/a/path", "no host"),
        ("host:", "no path"),
        (":/path", "no host"),
        ("host:relative/path", "not absolute"),
    ):
        with pytest.raises(sync.SyncError):
            sync.Destination.parse(bad), why


def test_snapshot_is_consistent_while_a_writer_is_mid_transaction(tmp_path):
    """The reason this uses VACUUM INTO rather than copying the file.

    The index is WAL-mode, so a writer's uncommitted rows live in -wal, not in
    the main file. A snapshot must contain the committed rows and none of the
    uncommitted ones -- copying either file alone gets that wrong.
    """
    src = tmp_path / "index.sqlite"
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    conn.commit()

    # A writer holding an open transaction, exactly like a running scan.
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t VALUES (999)")

    dest = tmp_path / "snap.sqlite"
    sync.snapshot_index(src, dest)

    out = sqlite3.connect(dest)
    rows = [r[0] for r in out.execute("SELECT n FROM t ORDER BY n")]
    out.close()
    conn.rollback()
    conn.close()

    assert rows == list(range(100)), "snapshot must hold every committed row"
    assert 999 not in rows, "and none of an in-flight transaction's"


def test_snapshot_replaces_an_existing_file(tmp_path):
    src = tmp_path / "index.sqlite"
    sqlite3.connect(src).execute("CREATE TABLE t (n INTEGER)")
    dest = tmp_path / "snap.sqlite"
    dest.write_bytes(b"stale rubbish that is not a database")
    sync.snapshot_index(src, dest)
    sqlite3.connect(dest).execute("SELECT * FROM t")   # would raise if stale


def test_sync_refuses_without_an_index(tmp_path):
    """Better than rsyncing gigabytes and then failing at the last step."""
    photos = tmp_path / "pictures"
    photos.mkdir()
    cfg = fixtures.make_config(tmp_path, photos)
    dest = sync.Destination.parse("h:/srv/x")
    with pytest.raises(sync.SyncError, match="scan"):
        sync.run(cfg, dest)


def test_images_are_sent_before_the_index(tmp_path, monkeypatch):
    """The ordering the whole module exists to get right.

    An index that lands first names images that have not arrived, and every
    thumbnail on the live site breaks until rsync catches up.
    """
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(800, 600))
    cfg = fixtures.make_config(tmp_path, photos)
    db.create_index(cfg.index_db).close()
    cfg.derived_root.mkdir(parents=True, exist_ok=True)

    calls: list[list[str]] = []
    monkeypatch.setattr(sync, "_run", lambda cmd, **kw: calls.append(cmd))

    sync.run(cfg, sync.Destination.parse("h:/srv/x"))

    order = [" ".join(c) for c in calls]
    sent_images = next(i for i, c in enumerate(order) if "derived/" in c)
    swapped_index = next(i for i, c in enumerate(order) if " mv " in f" {c} ")
    assert sent_images < swapped_index, order


def test_the_index_is_swapped_in_with_a_rename(tmp_path, monkeypatch):
    """Not copied over in place: a request must never see a half-written file."""
    photos = tmp_path / "pictures"
    photos.mkdir()
    cfg = fixtures.make_config(tmp_path, photos)
    db.create_index(cfg.index_db).close()
    cfg.derived_root.mkdir(parents=True, exist_ok=True)

    calls: list[list[str]] = []
    monkeypatch.setattr(sync, "_run", lambda cmd, **kw: calls.append(cmd))
    sync.run(cfg, sync.Destination.parse("h:/srv/x"))

    mv = next(c for c in calls if "mv" in c)
    assert mv[:2] == ["ssh", "h"]
    assert mv[-2].endswith(".incoming")
    assert mv[-1] == "/srv/x/index.sqlite"

    # The old -wal describes the file that was just replaced; leaving it makes
    # SQLite try to recover it against the new database.
    rm = next(c for c in calls if "rm" in c)
    assert any(a.endswith("-wal") for a in rm)
    assert any(a.endswith("-shm") for a in rm)
