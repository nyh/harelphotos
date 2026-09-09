"""Scanner tests (DESIGN.md 8, 16).

The highest-value tests in the project: a rescan must converge on exactly what
a from-scratch scan produces, and must not do work it doesn't need to.
"""

from __future__ import annotations

import os
import time

import pytest

from harelphotos import acl, db, lock, scanner

from . import fixtures


@pytest.fixture
def tree(tmp_path):
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    yield cfg, conn, photos
    conn.close()


def snapshot(conn):
    """Everything a scan should determine, for comparing two scans."""
    dirs = {
        r["path"]: (r["title"], r["natkey"], r["acl_chain"], r["n_photos"],
                    r["n_photos_rec"], r["hidden"], r["location"])
        for r in conn.execute("SELECT * FROM dirs")
    }
    photos = {
        (r["dir_id"], r["name"]): (r["size"], r["width"], r["height"], r["taken"],
                                   r["content_sig"])
        for r in conn.execute("SELECT * FROM photos")
    }
    return dirs, photos


def test_scan_finds_the_photos(tree):
    cfg, conn, _ = tree
    stats = scanner.scan(cfg, conn)
    # 6 real photos: 2019/01 x2, 2019/02 x4 (c, d, UPPER.JPG, unicode),
    # private x1, ancient x1 = 8
    assert stats.photos_seen == 8
    assert stats.photos_added == 8
    names = {r["name"] for r in conn.execute("SELECT name FROM photos")}
    assert "a.jpg" in names
    assert "UPPER.JPG" in names          # extension match is case-insensitive
    assert "שלום.jpg" in names           # unicode names are ordinary names
    assert "notes.txt" not in names
    assert "Thumbs.db" not in names
    assert "clip.mp4" not in names


def test_dotfiles_and_excludes_are_skipped(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    paths = {r["path"] for r in conn.execute("SELECT path FROM dirs")}
    assert ".hidden" not in paths        # matched by the ".*" exclude


def test_album_toml_is_applied(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    row = conn.execute("SELECT * FROM dirs WHERE path = '2019'").fetchone()
    assert row["title"] == "Twenty Nineteen"
    assert row["dirsort"] == "-name"
    row = conn.execute("SELECT * FROM dirs WHERE path = 'ancient'").fetchone()
    assert row["sort_key"] == "1975"
    assert row["natkey"].startswith("0000001975")   # sorts as if named 1975
    assert row["location"] == "Haifa, Israel"


def test_acl_chain_is_stored_and_inherited(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    chain = acl.loads(
        conn.execute("SELECT acl_chain FROM dirs WHERE path = 'private'").fetchone()["acl_chain"]
    )
    assert acl.can_view(chain, "nyh", {})
    assert not acl.can_view(chain, "sis", {})
    # An unrestricted directory really is unrestricted.
    chain = acl.loads(
        conn.execute("SELECT acl_chain FROM dirs WHERE path = '2019'").fetchone()["acl_chain"]
    )
    assert chain == ()


def test_exif_dates_and_dimensions(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    row = conn.execute("SELECT * FROM photos WHERE name = 'a.jpg'").fetchone()
    assert row["width"] == 64 and row["height"] == 48
    assert row["taken"] is not None
    assert time.strftime("%Y-%m-%d", time.localtime(row["taken"])) == "2019-01-15"
    # A photo with no EXIF date is indexed with taken = NULL; the sort falls
    # back to mtime (DESIGN.md 5.3).
    row = conn.execute("SELECT * FROM photos WHERE name = 'd.jpg'").fetchone()
    assert row["taken"] is None
    assert row["width"] == 64


def test_rollup_counts_recursively(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    root = conn.execute("SELECT * FROM dirs WHERE path = ''").fetchone()
    assert root["n_photos_rec"] == 8
    y = conn.execute("SELECT * FROM dirs WHERE path = '2019'").fetchone()
    assert y["n_photos"] == 0            # no photos directly in 2019/
    assert y["n_photos_rec"] == 6        # but six beneath it
    m = conn.execute("SELECT * FROM dirs WHERE path = '2019/01'").fetchone()
    assert m["n_photos"] == 2


def test_directory_with_no_photos_beneath_is_visible_but_counted_zero(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    row = conn.execute("SELECT * FROM dirs WHERE path = 'movies'").fetchone()
    assert row is not None
    assert row["n_photos_rec"] == 0      # the web layer uses this to hide it


def test_cover_resolution(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    # Explicit cover.
    row = conn.execute("SELECT * FROM dirs WHERE path = 'ancient'").fetchone()
    cover = conn.execute("SELECT name FROM photos WHERE id = ?", (row["cover_photo"],)).fetchone()
    assert cover["name"] == "old.jpg"
    # Implicit: first photo by name.
    row = conn.execute("SELECT * FROM dirs WHERE path = '2019/01'").fetchone()
    cover = conn.execute("SELECT name FROM photos WHERE id = ?", (row["cover_photo"],)).fetchone()
    assert cover["name"] == "a.jpg"
    # A directory with no photos of its own borrows a child's cover.
    row = conn.execute("SELECT * FROM dirs WHERE path = '2019'").fetchone()
    assert row["cover_photo"] is not None


def test_missing_cover_is_reported_not_fatal(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "x" / "real.jpg")
    (photos / "x" / ".album.toml").write_text('cover = "ghost.jpg"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    row = conn.execute("SELECT * FROM dirs WHERE path = 'x'").fetchone()
    assert row["cover_spec"] == "ghost.jpg"
    assert row["cover_photo"] is not None      # fell back to the real photo
    conn.close()


# --------------------------------------------------------------- convergence

def test_rescan_is_a_no_op(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    before = snapshot(conn)
    stats = scanner.scan(cfg, conn)
    assert stats.photos_checked == 0     # nothing re-read
    assert stats.photos_added == 0
    assert stats.photos_removed == 0
    assert snapshot(conn) == before


def test_touching_every_file_re_reads_but_regenerates_nothing(tree):
    """The jhead -ft trap (DESIGN.md 8 phase 2).

    Rewriting every mtime must NOT be mistaken for 'every photo changed'. The
    headers get re-read (mtime said to look), but the content signatures come
    back identical, so nothing downstream is invalidated.
    """
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    sigs_before = {
        r["id"]: r["content_sig"] for r in conn.execute("SELECT id, content_sig FROM photos")
    }
    future = time.time() + 10_000
    for p in photos.rglob("*.jpg"):
        os.utime(p, (future, future))
    for p in photos.rglob("*.JPG"):
        os.utime(p, (future, future))

    stats = scanner.scan(cfg, conn)
    assert stats.photos_checked == 8         # mtime changed, so we looked
    assert stats.photos_unchanged == 8       # ...and the bytes were identical
    assert stats.photos_changed == 0         # ...so nothing needs re-encoding
    sigs_after = {
        r["id"]: r["content_sig"] for r in conn.execute("SELECT id, content_sig FROM photos")
    }
    assert sigs_after == sigs_before
    # The new mtimes are stored, so a third scan looks at nothing at all.
    assert scanner.scan(cfg, conn).photos_checked == 0


def test_changing_bytes_is_detected(tree):
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    fixtures.make_jpeg(photos / "2019" / "01" / "a.jpg", size=(80, 60), colour=(10, 200, 30))
    stats = scanner.scan(cfg, conn)
    assert stats.photos_changed == 1
    row = conn.execute("SELECT * FROM photos WHERE name = 'a.jpg'").fetchone()
    assert (row["width"], row["height"]) == (80, 60)


def test_added_and_deleted_files(tree):
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    fixtures.make_jpeg(photos / "2019" / "01" / "new.jpg", taken="2019:01:20 08:00:00")
    (photos / "2019" / "01" / "b.jpg").unlink()
    stats = scanner.scan(cfg, conn)
    assert stats.photos_added == 1
    assert stats.photos_removed == 1
    names = {r["name"] for r in conn.execute("SELECT name FROM photos")}
    assert "new.jpg" in names and "b.jpg" not in names


def test_deleting_a_directory_removes_its_photos(tree):
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    import shutil

    shutil.rmtree(photos / "2019" / "01")
    stats = scanner.scan(cfg, conn)
    assert stats.dirs_removed == 1
    assert stats.photos_removed == 2
    assert conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"] == 6


def test_moving_a_subtree_converges(tree):
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    (photos / "2019" / "01").rename(photos / "2019" / "01-moved")
    scanner.scan(cfg, conn)
    paths = {r["path"] for r in conn.execute("SELECT path FROM dirs")}
    assert "2019/01-moved" in paths and "2019/01" not in paths


def test_rescan_matches_a_scan_from_scratch(tree, tmp_path):
    """The convergence property: incremental must equal from-scratch."""
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    fixtures.make_jpeg(photos / "2020" / "new.jpg", taken="2020:03:03 03:03:03")
    (photos / "ancient" / "old.jpg").unlink()
    scanner.scan(cfg, conn)
    incremental = snapshot(conn)

    cfg2 = fixtures.make_config(tmp_path / "second", photos)
    conn2 = fixtures.fresh_index(cfg2)
    scanner.scan(cfg2, conn2)
    fresh = snapshot(conn2)
    conn2.close()

    # Row ids differ between the two databases, so compare by path/name.
    assert set(incremental[0]) == set(fresh[0])
    for path in incremental[0]:
        assert incremental[0][path] == fresh[0][path], path
    assert {n for _, n in incremental[1]} == {n for _, n in fresh[1]}


def test_editing_album_toml_updates_the_row(tree):
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    (photos / "2019" / ".album.toml").write_text('title = "Renamed"\n', encoding="utf-8")
    scanner.scan(cfg, conn)
    row = conn.execute("SELECT * FROM dirs WHERE path = '2019'").fetchone()
    assert row["title"] == "Renamed"
    assert row["dirsort"] is None          # the removed key really is removed


def test_adding_a_restriction_later_propagates_to_the_subtree(tree):
    cfg, conn, photos = tree
    scanner.scan(cfg, conn)
    (photos / "2019" / ".album.toml").write_text('allow = ["nyh"]\n', encoding="utf-8")
    scanner.scan(cfg, conn)
    for path in ("2019", "2019/01", "2019/02"):
        chain = acl.loads(
            conn.execute("SELECT acl_chain FROM dirs WHERE path = ?", (path,)).fetchone()[0]
        )
        assert not acl.can_view(chain, "sis", {}), path


# ------------------------------------------------------------------ failures

def test_corrupt_jpeg_is_recorded_not_raised(tree):
    cfg, conn, photos = tree
    (photos / "2019" / "01" / "broken.jpg").write_bytes(b"\xff\xd8 not really a jpeg")
    stats = scanner.scan(cfg, conn)
    assert stats.photos_failed == 1
    row = conn.execute("SELECT * FROM photos WHERE name = 'broken.jpg'").fetchone()
    assert row["deriv_error"]
    # ...and every other photo was still indexed.
    assert conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"] == 9


def test_malformed_album_toml_is_recorded_not_raised(tree):
    cfg, conn, photos = tree
    (photos / "2019" / "02" / ".album.toml").write_text('title = "unclosed', encoding="utf-8")
    stats = scanner.scan(cfg, conn)
    assert stats.config_errors
    row = conn.execute("SELECT * FROM dirs WHERE path = '2019/02'").fetchone()
    assert "invalid TOML" in row["cfg_error"]
    assert row["title"] is None            # degraded to defaults, still scanned


def test_scanning_a_subdirectory_only(tree):
    cfg, conn, _ = tree
    stats = scanner.scan(cfg, conn, subpath="2019/01")
    assert stats.photos_seen == 2
    # ...and it must not delete rows outside the subtree it was asked about.
    scanner.scan(cfg, conn)
    stats = scanner.scan(cfg, conn, subpath="2019/01")
    assert stats.photos_removed == 0
    assert conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"] == 8


def test_limit_stops_early(tree):
    cfg, conn, _ = tree
    stats = scanner.scan(cfg, conn, limit=3)
    assert stats.photos_checked == 3
    # The rest stay pending, so a later run finishes the job.
    assert scanner.scan(cfg, conn).photos_checked == 5


def test_full_rereads_every_header(tree):
    cfg, conn, _ = tree
    scanner.scan(cfg, conn)
    stats = scanner.scan(cfg, conn, full=True)
    assert stats.photos_checked == 8       # every header re-read...
    assert stats.photos_unchanged == 8     # ...and correctly found unchanged


def test_missing_photo_root_is_an_error(tmp_path):
    cfg = fixtures.make_config(tmp_path, tmp_path / "nope")
    conn = fixtures.fresh_index(cfg)
    with pytest.raises(NotADirectoryError):
        scanner.scan(cfg, conn)
    conn.close()


# ---------------------------------------------------------------------- lock

def test_lock_is_exclusive(tmp_path):
    path = tmp_path / "scan.lock"
    with lock.ScanLock(path):
        with pytest.raises(lock.LockBusy, match="in progress"):
            with lock.ScanLock(path):
                pass


def test_lock_is_released_on_exit(tmp_path):
    path = tmp_path / "scan.lock"
    with lock.ScanLock(path):
        pass
    with lock.ScanLock(path):
        pass          # acquiring again must simply work


def test_lock_message_names_the_holder(tmp_path):
    path = tmp_path / "scan.lock"
    with lock.ScanLock(path):
        with pytest.raises(lock.LockBusy, match=f"pid {os.getpid()}"):
            with lock.ScanLock(path):
                pass


def test_break_lock(tmp_path):
    path = tmp_path / "scan.lock"
    assert lock.break_lock(path) is False
    path.write_text("stale")
    assert lock.break_lock(path) is True
