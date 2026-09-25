"""Kept index connections.

The web process opened SQLite on every request -- about 0.9 ms, a tenth of the
server time of a thumbnail, and a page of lazily-loaded thumbnails asks for a
hundred of those. The connection is now kept per thread, which is only safe as
long as the things that can change underneath it are still noticed.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import re
import sqlite3
import threading

import pytest

from harelphotos import db, scanner
from harelphotos.web import create_app

from . import fixtures


@pytest.fixture
def cfg(tmp_path):
    photos = tmp_path / "pictures"
    for i in range(3):
        fixtures.make_jpeg(photos / "a" / f"{i}.jpg")
    c = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(c)
    scanner.scan(c, conn)
    conn.close()
    return c


def test_the_same_connection_serves_the_next_request(cfg):
    readers = db.Readers(cfg.index_db)
    first = readers.connection()
    assert readers.connection() is first
    assert readers.connection() is first
    readers.close()


def test_each_thread_gets_its_own(cfg):
    """A sqlite3 connection cannot be shared between threads, and gunicorn
    runs eight of them per worker here -- a browser fetching a grid of
    thumbnails over HTTP/2 has many in flight at once."""
    readers = db.Readers(cfg.index_db)
    seen = {}

    def work(n):
        seen[n] = readers.connection()
        # And it is usable from that thread, which a shared one would not be.
        seen[n].execute("SELECT count(*) FROM photos").fetchone()
        readers.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 8
    assert len({id(c) for c in seen.values()}) == 8, "connections were shared"


def test_parallel_requests_are_served(cfg):
    """The real arrangement: many requests at once, each on its own thread."""
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    results = {}

    def fetch(n):
        results[n] = app.test_client().get("/a/a/").status_code

    threads = [threading.Thread(target=fetch, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(results.values()) == {200}, results


def test_an_upgrade_underneath_a_kept_connection_is_noticed(cfg):
    """A scan can migrate the index in place while the server runs, and the
    file keeps its inode when it does. Missing that would mean serving from a
    connection whose schema is not the one the code expects -- and would undo
    the page that explains the mismatch instead of failing every request."""
    readers = db.Readers(cfg.index_db)
    assert readers.connection() is not None

    conn = sqlite3.connect(cfg.index_db)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                 (str(db.SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()

    with pytest.raises(db.SchemaMismatch):
        readers.connection()
    readers.close()


def test_a_rebuilt_index_is_noticed(cfg):
    """"Delete it and run scan" is documented advice for an index that cannot
    be migrated. That leaves a kept connection reading an unlinked inode which
    no longer reflects anything -- every query would answer from a file that
    is no longer there."""
    readers = db.Readers(cfg.index_db)
    before = readers.connection()
    assert before.execute("SELECT count(*) FROM photos").fetchone()[0] == 3

    cfg.index_db.unlink()
    conn = fixtures.fresh_index(cfg)          # a new file, new inode
    conn.close()

    after = readers.connection()
    assert after is not before, "kept a connection to the deleted file"
    assert after.execute("SELECT count(*) FROM photos").fetchone()[0] == 0
    readers.close()


def test_the_page_still_explains_a_mismatch(cfg):
    """End to end: the 503 page built for this survives the connection being
    kept, which is the thing most easily lost by caching it."""
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()
    assert c.get("/a/").status_code == 200

    conn = sqlite3.connect(cfg.index_db)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                 (str(db.SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()

    r = c.get("/a/")
    assert r.status_code == 503
    assert "Just a moment" in r.get_data(as_text=True)


def test_the_page_cache_is_bounded(cfg):
    """Sixteen connections at SQLite's default 2 MB is 33 MB, against the 82 MB
    two workers cost on a machine with a gigabyte. Measured at 512 KiB neither
    a photo lookup nor a 3505-row album query was slower, because the operating
    system's page cache is what keeps the file warm and it is shared."""
    readers = db.Readers(cfg.index_db)
    conn = readers.connection()
    # Negative means KiB rather than pages, which is the whole point of it.
    assert conn.execute("PRAGMA cache_size").fetchone()[0] == -db.Readers.CACHE_KIB
    readers.close()
