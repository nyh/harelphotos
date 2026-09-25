"""Guest accounts: `only` in users.toml, which inverts the default.

An ordinary account sees everything except what an `allow` list keeps from it.
A guest account sees *nothing* except the subtrees it was granted -- so a
directory added to the collection tomorrow is invisible to it without anyone
having had to remember.

Most of this file is the negative half. The rule is only worth anything if the
directories a guest was not granted are genuinely unreachable, by URL as much
as by listing, and for images and originals as much as for pages.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import re

import pytest

from harelphotos import db, scanner, users as users_mod
from harelphotos.queries import Index, Viewer
from harelphotos.web import create_app

from . import fixtures

GUEST = "nyh@scylladb.com"


def build(tmp_path, only, extra=(), groups=None):
    """A small collection, and a guest granted `only`."""
    import dataclasses

    photos = tmp_path / "pictures"
    for d in ("2026/07/thailand", "2026/07/thailand/day1", "2026/07/bangkok",
              "2019/08/naxos", "family/kids"):
        for i in range(2):
            fixtures.make_jpeg(photos / d / f"{i}.jpg")
    fixtures.make_jpeg(photos / "loose.jpg")          # a photo at the very top
    for path, text in extra:
        (photos / path).parent.mkdir(parents=True, exist_ok=True)
        (photos / path).write_text(text, encoding="utf-8")

    cfg = fixtures.make_config(tmp_path, photos)
    if groups:
        cfg = dataclasses.replace(cfg, groups=groups)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    fixtures.add_user(cfg, token="dad", password="pw")
    us = users_mod.load(cfg.users_file)
    table = dict(us.by_token)
    table[GUEST] = users_mod.User(token=GUEST, name="Guest",
                                  password_hash=users_mod.hash_password("pw"),
                                  only=tuple(only), _only_given=True)
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))
    return cfg


def guest(only=("2026/07/thailand",)):
    return Viewer(token=GUEST, name="Guest", only=tuple(only), is_guest=True)


def index_for(cfg):
    conn = db.open_index(cfg.index_db, read_only=True)
    return conn, Index(conn, cfg)


def client_as(cfg, who, password="pw"):
    app = create_app(cfg, require_login=True)
    app.config.update(TESTING=True)
    c = app.test_client()
    page = c.get("/").get_data(as_text=True)
    csrf = re.search(r'name="csrf" value="([^"]*)"', page).group(1)
    r = c.post("/login", data={"csrf": csrf, "username": who, "password": password})
    assert r.status_code in (302, 303), f"{who} could not log in"
    return c


# ------------------------------------------------------- what a guest sees

def test_the_front_page_is_exactly_the_granted_directories(tmp_path):
    cfg = build(tmp_path, ["2026/07/thailand", "2019/08/naxos"])
    conn, ix = index_for(cfg)
    v = guest(["2026/07/thailand", "2019/08/naxos"])
    root = ix.album("", v)
    assert root is not None, "a guest must have a page to land on"
    assert [a.path for a in ix.subalbums(root, v)] == [
        "2026/07/thailand", "2019/08/naxos"]
    conn.close()


def test_a_grant_includes_everything_beneath_it(tmp_path):
    cfg = build(tmp_path, ["2026/07/thailand"])
    conn, ix = index_for(cfg)
    v = guest()
    assert ix.album("2026/07/thailand", v) is not None
    assert ix.album("2026/07/thailand/day1", v) is not None
    assert ix.photo("2026/07/thailand/day1/0.jpg", v) is not None
    conn.close()


def test_the_ancestors_of_a_grant_are_not_visible(tmp_path):
    """Reaching into the middle of the tree does not open the way down to it."""
    cfg = build(tmp_path, ["2026/07/thailand"])
    conn, ix = index_for(cfg)
    v = guest()
    for path in ("2026", "2026/07"):
        assert ix.album(path, v) is None, path
    conn.close()


# --------------------------------------------------- what a guest must not

def test_a_sibling_of_the_grant_is_unreachable(tmp_path):
    cfg = build(tmp_path, ["2026/07/thailand"])
    conn, ix = index_for(cfg)
    v = guest()
    assert ix.album("2026/07/bangkok", v) is None
    assert ix.photo("2026/07/bangkok/0.jpg", v) is None
    conn.close()


def test_unrelated_directories_are_unreachable(tmp_path):
    cfg = build(tmp_path, ["2026/07/thailand"])
    conn, ix = index_for(cfg)
    v = guest()
    for path in ("2019", "2019/08/naxos", "family", "family/kids"):
        assert ix.album(path, v) is None, path
    assert ix.photo("family/kids/0.jpg", v) is None
    conn.close()


def test_a_directory_added_later_is_invisible_without_anyone_remembering(tmp_path):
    """The entire reason for the feature. A new top-level album must not
    become visible to a guest because nobody thought to exclude it."""
    cfg = build(tmp_path, ["2026/07/thailand"])
    photos = tmp_path / "pictures"
    for i in range(2):
        fixtures.make_jpeg(photos / "2027" / "secret" / f"{i}.jpg")
    conn = db.open_index(cfg.index_db)
    scanner.scan(cfg, conn)
    conn.close()

    conn, ix = index_for(cfg)
    v = guest()
    assert ix.album("2027", v) is None
    assert ix.album("2027/secret", v) is None
    assert ix.photo("2027/secret/0.jpg", v) is None
    # ...while the household sees it with no action taken.
    dad = Viewer(token="dad", name="Dad")
    assert ix.album("2027/secret", dad) is not None
    conn.close()


def test_the_doorway_offers_none_of_its_own_photographs(tmp_path):
    """The top of the tree opens for a guest so they have a page to land on.
    That must not hand them the loose photographs sitting in it."""
    cfg = build(tmp_path, ["2026/07/thailand"])
    conn, ix = index_for(cfg)
    v = guest()
    root = ix.album("", v)
    assert ix.photos(root, v) == []
    assert ix.photo("loose.jpg", v) is None
    # The household still sees it.
    assert ix.photo("loose.jpg", Viewer(token="dad", name="Dad")) is not None
    conn.close()


def test_a_grant_of_nothing_grants_nothing(tmp_path):
    cfg = build(tmp_path, [])
    conn, ix = index_for(cfg)
    v = guest([])
    root = ix.album("", v)
    assert root is not None                      # still has a page
    assert ix.subalbums(root, v) == []           # with nothing on it
    assert ix.photos(root, v) == []
    assert ix.album("2026/07/thailand", v) is None
    conn.close()


def test_a_grant_matches_whole_path_segments_only(tmp_path):
    """`2026/07` must not open `2026/07b`, which string prefixes would."""
    photos = tmp_path / "pictures"
    cfg = build(tmp_path, ["2026/07"])
    for i in range(2):
        fixtures.make_jpeg(photos / "2026" / "07b" / f"{i}.jpg")
    conn = db.open_index(cfg.index_db)
    scanner.scan(cfg, conn)
    conn.close()

    conn, ix = index_for(cfg)
    v = guest(["2026/07"])
    assert ix.album("2026/07/thailand", v) is not None
    assert ix.album("2026/07b", v) is None
    conn.close()


# ------------------------------------------- how grants meet allow-lists

def test_a_private_subdirectory_of_a_granted_album_stays_private(tmp_path):
    """A grant opens a subtree; it does not blind the allow-lists inside it."""
    cfg = build(tmp_path, ["2026/07/thailand"],
                extra=[("2026/07/thailand/day1/.album.toml", 'allow = ["dad"]\n')])
    conn, ix = index_for(cfg)
    v = guest()
    assert ix.album("2026/07/thailand", v) is not None      # the grant itself
    assert ix.album("2026/07/thailand/day1", v) is None     # but not this
    assert ix.photo("2026/07/thailand/day1/0.jpg", v) is None
    conn.close()


def test_a_grant_reaches_into_a_restricted_tree(tmp_path):
    """Restrictions *above* a grant are discarded: naming the path was a
    deliberate act, and requiring the guest to satisfy the ancestors as well
    would make the grant silently do nothing -- the failure this exists to
    avoid."""
    cfg = build(tmp_path, ["2026/07/thailand"],
                extra=[("2026/.album.toml", 'allow = ["@family"]\n')],
                groups={"family": ("dad",)})
    conn, ix = index_for(cfg)
    v = guest()
    assert ix.album("2026/07/thailand", v) is not None
    assert ix.album("2026", v) is None            # still not the tree above it
    conn.close()


# ------------------------------------------------------- over the network

@pytest.mark.parametrize("url", [
    "/a/2026/07/bangkok/",
    "/a/2019/08/naxos/",
    "/a/family/kids/",
    "/a/2026/",
    "/p/2026/07/bangkok/0.jpg",
    "/p/family/kids/0.jpg",
    "/p/loose.jpg",
    "/orig/2026/07/bangkok/0.jpg",
    "/orig/family/kids/0.jpg",
])
def test_a_guest_is_refused_over_http(tmp_path, url):
    cfg = build(tmp_path, ["2026/07/thailand"])
    c = client_as(cfg, GUEST)
    assert c.get(url).status_code == 404, url


def test_the_images_themselves_are_refused_too(tmp_path):
    """The pages being refused is not enough: a thumbnail URL is guessable
    from a file name, so the image routes carry the same check."""
    cfg = build(tmp_path, ["2026/07/thailand"])
    c = client_as(cfg, GUEST)
    for url in ("/i/256/2026/07/bangkok/0.jpg",
                "/i/512/family/kids/0.jpg",
                "/i/orig/2026/07/bangkok/0.jpg"):
        assert c.get(url).status_code in (401, 404), url
    # ...and the granted album's own images do work.
    assert c.get("/i/256/2026/07/thailand/0.jpg").status_code == 200


def test_the_guests_front_page_lists_the_grant_and_nothing_else(tmp_path):
    cfg = build(tmp_path, ["2026/07/thailand"])
    c = client_as(cfg, GUEST)
    body = c.get("/a/").get_data(as_text=True)
    assert c.get("/a/").status_code == 200
    assert "/a/2026/07/thailand/" in body
    for absent in ("/a/2026/07/bangkok/", "/a/2019/", "/a/family/", "loose.jpg"):
        assert absent not in body, absent


def test_the_household_is_unaffected(tmp_path):
    """The whole point is that this changes nothing for everybody else."""
    cfg = build(tmp_path, ["2026/07/thailand"])
    c = client_as(cfg, "dad")
    for url in ("/a/", "/a/2026/", "/a/2026/07/bangkok/", "/a/family/kids/",
                "/p/loose.jpg", "/orig/family/kids/0.jpg"):
        assert c.get(url).status_code == 200, url


# ------------------------------------------------------------ the account

def test_an_account_cannot_be_both_admin_and_confined(tmp_path):
    """An admin bypasses every check, so the two together would read as a
    restriction and be none at all."""
    with pytest.raises(users_mod.UsersError, match="admin"):
        users_mod.parse('[users.x]\npassword = "p"\nadmin = true\nonly = ["a"]\n')


def test_only_is_normalised_and_distinguishable_from_absent():
    us = users_mod.parse(
        '[users.a]\npassword = "p"\n'
        '[users.b]\npassword = "p"\nonly = ["/2026/07/thailand/", "", "x"]\n')
    assert us.get("a").only == () and not us.get("a").is_guest
    assert us.get("b").only == ("2026/07/thailand", "x")
    assert us.get("b").is_guest


def test_an_empty_only_still_means_confined():
    """`only = []` is a guest who has been granted nothing, not a member of
    the household -- the two look identical in the tuple, so the parser has to
    keep the difference."""
    us = users_mod.parse('[users.b]\npassword = "p"\nonly = []\n')
    assert us.get("b").only == ()
    assert us.get("b").is_guest


def test_only_must_be_a_list_of_strings():
    for bad in ('only = "2026"', 'only = [1, 2]', 'only = 5'):
        with pytest.raises(users_mod.UsersError, match="only"):
            users_mod.parse(f'[users.x]\npassword = "p"\n{bad}\n')


def test_check_names_a_grant_that_matches_nothing(tmp_path):
    """A mistyped path grants nothing and looks exactly like an account you
    have not shared anything with yet, so it has to be said out loud."""
    from harelphotos import check as check_mod

    cfg = build(tmp_path, ["2026/07/thailand", "2026/07/thialand"])
    conn = db.open_index(cfg.index_db, read_only=True)
    r = check_mod.run(cfg, conn)
    conn.close()

    assert r.dead_grants == [(GUEST, "2026/07/thialand")]
    assert r.problems >= 1


@pytest.mark.parametrize("raw", ['[""]', '["/"]', '["///"]', '["", "2026/07/thailand"]'])
def test_an_empty_grant_cannot_stand_for_the_whole_collection(raw):
    """`only = [""]` must not read as "granted the root", which would hand over
    everything. It is dropped, leaving an account with one grant fewer -- and
    an account with none sees nothing, so the mistake fails closed."""
    u = users_mod.parse(f'[users.g]\npassword = "x"\nonly = {raw}\n').get("g")
    assert "" not in u.only
    assert u.is_guest                       # still confined, never promoted
    assert u.only in ((), ("2026/07/thailand",))
