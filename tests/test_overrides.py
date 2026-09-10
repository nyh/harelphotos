"""Album settings the server writes for itself (DESIGN.md 5.3).

The point of this layer is that the photo tree is read-only to this software,
so the tests that matter are: the tree is never written, `.album.toml` still
works and still wins where no override exists, and a pick made in the interface
takes effect without a scan.
"""

from __future__ import annotations

import re

import pytest

from harelphotos import album, db, overrides, queries, scanner
from harelphotos.web import create_app

from . import fixtures


@pytest.fixture
def project(tmp_path):
    photos = tmp_path / "pictures"
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        fixtures.make_jpeg(photos / "trip" / name, size=(120, 90))
    fixtures.make_jpeg(photos / "trip" / "junk" / "deep" / "x.jpg", size=(100, 80))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    fixtures.add_user(cfg, "boss", "pw", admin=True)
    fixtures.add_user(cfg, "sis", "pw")
    return cfg


def index_for(cfg):
    conn = db.open_index(cfg.index_db, read_only=True)
    return conn, queries.Index(conn, cfg)


def admin():
    return queries.Viewer(token="boss", name="Boss", is_admin=True)


def rescan(cfg, subpath=""):
    conn = db.open_index(cfg.index_db)
    scanner.scan(cfg, conn, subpath=subpath, headers_only=True)
    conn.close()


def client_as(cfg, name):
    app = create_app(cfg, require_login=True)
    app.config.update(TESTING=True)
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    c.post("/login", data={"username": name, "password": "pw", "csrf": token})
    return c


# ---------------------------------------------------------------- the file

def test_the_photo_tree_is_never_written(project):
    before = {p: p.stat().st_mtime_ns
              for p in project.photo_root.rglob("*") if p.is_file()}
    overrides.set_for(project, "trip", cover="b.jpg")
    overrides.set_for(project, "trip/junk", hidden=True)
    after = {p: p.stat().st_mtime_ns
             for p in project.photo_root.rglob("*") if p.is_file()}
    assert after == before


def test_entries_do_not_disturb_each_other(project):
    overrides.set_for(project, "trip", cover="b.jpg")
    overrides.set_for(project, "trip/junk", hidden=True)
    overrides.set_for(project, "trip", allow=("boss",))
    table = overrides.load(project)
    assert table["trip"].cover == "b.jpg"       # not lost by the second write
    assert table["trip"].allow == ("boss",)
    assert table["trip/junk"].hidden is True


def test_an_emptied_entry_is_removed_entirely(project):
    overrides.set_for(project, "trip", cover="b.jpg")
    overrides.set_for(project, "trip", cover=None)
    assert "trip" not in overrides.load(project)


def test_the_root_album_has_a_visible_key(project):
    """TOML allows "" as a key, but a file people read is better with a mark."""
    overrides.set_for(project, "", cover="a.jpg")
    text = project.overrides_file.read_text(encoding="utf-8")
    assert '["."]' in text
    assert overrides.get(project, "").cover == "a.jpg"


def test_a_damaged_file_is_ignored_rather_than_fatal(project):
    project.overrides_file.write_text("[[[not toml", encoding="utf-8")
    assert overrides.load(project) == {}


def test_rereads_when_the_file_changes(project):
    """Cached by mtime, because the album page consults it on every request --
    but a pick made a moment ago by another worker must still be seen."""
    assert overrides.get(project, "trip").cover is None
    overrides.set_for(project, "trip", cover="c.jpg")
    assert overrides.get(project, "trip").cover == "c.jpg"


# --------------------------------------------------------------- the cover

def test_a_pick_takes_effect_without_a_scan(project):
    """The whole reason covers resolve at request time. A click in the
    interface that needed a rescan to show would be no use at all."""
    conn, ix = index_for(project)
    auto = ix.cover_photo(ix.album("trip", admin())).name
    conn.close()

    overrides.set_for(project, "trip", cover="c.jpg")

    conn, ix = index_for(project)          # no scan in between
    assert ix.cover_photo(ix.album("trip", admin())).name == "c.jpg"
    conn.close()
    assert auto != "c.jpg", "the fixture did not actually change anything"


def test_a_pick_naming_a_missing_photo_falls_back(project):
    """A deleted photo must leave the album with its automatic cover, not a
    blank card."""
    overrides.set_for(project, "trip", cover="gone.jpg")
    conn, ix = index_for(project)
    assert ix.cover_photo(ix.album("trip", admin())) is not None
    conn.close()


def test_album_toml_still_sets_a_cover_and_the_override_wins(project):
    (project.photo_root / "trip" / album.ALBUM_FILE).write_text(
        'cover = "b.jpg"\n', encoding="utf-8")
    rescan(project, "trip")

    conn, ix = index_for(project)
    assert ix.cover_photo(ix.album("trip", admin())).name == "b.jpg"
    conn.close()

    overrides.set_for(project, "trip", cover="c.jpg")
    conn, ix = index_for(project)
    assert ix.cover_photo(ix.album("trip", admin())).name == "c.jpg"
    conn.close()

    # And removing the override hands it back to the hand-written setting.
    overrides.set_for(project, "trip", cover=None)
    conn, ix = index_for(project)
    assert ix.cover_photo(ix.album("trip", admin())).name == "b.jpg"
    conn.close()


# ------------------------------------------------------- only an admin may

def test_only_an_admin_may_set_a_cover(project):
    c = client_as(project, "sis")
    body = c.get("/p/trip/a.jpg").get_data(as_text=True)
    assert "Make cover" not in body

    token = re.search(r'name="csrf" value="([^"]+)"',
                      c.get("/a/trip/").get_data(as_text=True))
    r = c.post("/cover", data={"album": "trip", "photo": "a.jpg",
                               "csrf": token.group(1) if token else ""})
    assert r.status_code == 403
    assert overrides.get(project, "trip").cover is None


def test_an_admin_can_set_a_cover_from_the_photo_page(project):
    c = client_as(project, "boss")
    body = c.get("/p/trip/c.jpg").get_data(as_text=True)
    assert "Make cover" in body
    token = re.search(r'name="csrf" value="([^"]+)"', body).group(1)

    r = c.post("/cover", data={"album": "trip", "photo": "c.jpg", "csrf": token})
    assert r.status_code == 302
    assert overrides.get(project, "trip").cover == "c.jpg"


def test_a_cover_post_without_a_csrf_token_is_refused(project):
    c = client_as(project, "boss")
    r = c.post("/cover", data={"album": "trip", "photo": "c.jpg", "csrf": "wrong"})
    assert r.status_code == 400
    assert overrides.get(project, "trip").cover is None


def test_a_cover_must_name_a_photo_of_that_album(project):
    """Otherwise a pick could name a path, or somebody else's photo."""
    c = client_as(project, "boss")
    token = re.search(r'name="csrf" value="([^"]+)"',
                      c.get("/p/trip/a.jpg").get_data(as_text=True)).group(1)
    for bad in ("../secret.jpg", "junk/deep/x.jpg", "nope.jpg"):
        r = c.post("/cover", data={"album": "trip", "photo": bad, "csrf": token})
        assert r.status_code == 404, bad
    assert overrides.get(project, "trip").cover is None


# -------------------------------------------------------------- hiding

def test_hiding_a_directory_hides_everything_beneath_it(project):
    overrides.set_for(project, "trip/junk", hidden=True)
    rescan(project, "trip/junk")

    conn, ix = index_for(project)
    v = admin()
    assert ix.album("trip/junk", v) is None
    assert ix.album("trip/junk/deep", v) is None
    assert ix.photo("trip/junk/deep/x.jpg", v) is None
    listed = [a.path for a in ix.subalbums(ix.album("trip", v), v)]
    assert "trip/junk" not in listed
    conn.close()


def test_hidden_is_not_merely_unlisted(project):
    """It used to leave the album reachable by typing its URL, which makes a
    setting called "hidden" a trap."""
    overrides.set_for(project, "trip/junk", hidden=True)
    rescan(project, "trip/junk")
    c = client_as(project, "boss")           # even an admin
    assert c.get("/a/trip/junk/").status_code == 404
    assert c.get("/a/trip/junk/deep/").status_code == 404
    assert c.get("/p/trip/junk/deep/x.jpg").status_code == 404


def test_unhiding_brings_it_back(project):
    overrides.set_for(project, "trip/junk", hidden=True)
    rescan(project, "trip/junk")
    overrides.set_for(project, "trip/junk", hidden=None)
    rescan(project, "trip/junk")

    conn, ix = index_for(project)
    assert ix.album("trip/junk", admin()) is not None
    conn.close()
