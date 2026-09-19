"""Links between albums.

An otherwise empty directory whose `.album.toml` names other albums, so the
same photographs can be reached as "trips" and as "August 2026" without being
copied. A link is a shortcut: its card points at the target's own URL.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from harelphotos import album, db, queries, scanner
from harelphotos.web import create_app

from . import fixtures


def admin():
    return queries.Viewer(token=None, name="", is_admin=True)


@pytest.fixture
def tree(tmp_path):
    """Two real albums and a `trips/` that links to both."""
    photos = tmp_path / "pictures"
    for i in range(3):
        fixtures.make_jpeg(photos / "2026" / "07" / "thailand" / f"t{i}.jpg")
    for i in range(2):
        fixtures.make_jpeg(photos / "2019" / "08" / "naxos" / f"n{i}.jpg")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        'title = "Trips"\n'
        '[links]\n'
        '"2026 Thailand" = "2026/07/thailand"\n'
        '"Greece" = "2019/08/naxos"\n',
        encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    return cfg


def index_for(cfg):
    conn = db.open_index(cfg.index_db, read_only=True)
    return conn, queries.Index(conn, cfg)


# ------------------------------------------------------------- the counting

def test_a_links_album_counts_the_photographs_it_reaches(tree):
    conn, ix = index_for(tree)
    trips = ix.album("trips", admin())
    assert trips.n_photos_rec == 0            # none of its own
    assert trips.n_photos_linked == 5         # 3 in Thailand, 2 in Greece
    assert trips.n_photos_shown == 5          # what the card says
    conn.close()


def test_an_ancestor_never_counts_a_linked_photograph_twice(tree):
    """The point of keeping the two numbers apart.

    `trips/` and the albums it points at share the collection root. Letting
    linked photographs flow into `n_photos_rec` would have the root report ten
    where five exist, and every directory between would be wrong too.
    """
    conn, ix = index_for(tree)
    root = ix.album("", admin())
    assert root.n_photos_rec == 5
    assert root.n_photos_shown == 5
    conn.close()


def test_a_link_to_an_album_of_links_does_not_recurse(tmp_path):
    """The linked count sums targets' *tree* counts, never their linked ones,
    so a cycle adds real photographs once and stops."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "real" / "a.jpg")
    for name, target in (("a", "b"), ("b", "a")):
        (photos / name).mkdir(parents=True, exist_ok=True)
        (photos / name / ".album.toml").write_text(
            f'[links]\n"there" = "{target}"\n"real" = "real"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)                   # must terminate
    conn.close()
    conn, ix = index_for(cfg)
    assert ix.album("a", admin()).n_photos_linked == 1
    conn.close()


# --------------------------------------------------------------- the listing

def test_a_directory_with_only_links_is_still_an_album(tree):
    """It has no photographs of its own, and `subalbums()` drops anything with
    none anywhere beneath it. Links are the exception, and the whole point."""
    conn, ix = index_for(tree)
    listed = {a.path for a in ix.subalbums(ix.album("", admin()), admin())}
    assert "trips" in listed
    conn.close()


def test_a_link_card_is_named_by_the_link_and_covered_by_its_target(tree):
    conn, ix = index_for(tree)
    cards = ix.linked_albums(ix.album("trips", admin()), admin())
    assert [c.title for c in cards] == ["2026 Thailand", "Greece"]
    assert [c.path for c in cards] == ["2026/07/thailand", "2019/08/naxos"]
    assert all(c.cover is not None for c in cards)
    conn.close()


def test_the_card_points_at_the_targets_own_url(tree):
    """A link is a shortcut: clicking it leaves `trips` and lands on the real
    album, with its own breadcrumbs."""
    app = create_app(tree, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/trips/").get_data(as_text=True)
    assert "/a/2026/07/thailand/" in body
    assert "2026 Thailand" in body


def test_a_link_to_a_directory_that_has_gone_is_dropped(tmp_path):
    """A stale link is a missing card, never a broken one."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "real" / "a.jpg")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        '[links]\n"Real" = "real"\n"Gone" = "2030/nope"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/trips/").get_data(as_text=True)
    assert "Real" in body
    assert "Gone" not in body


# ------------------------------------------------------------- permissions

def test_a_link_to_a_restricted_album_is_invisible_to_whoever_may_not_see_it(tmp_path):
    """The link's own name would otherwise announce that a restricted album
    exists, which is exactly what answering 404 rather than 403 prevents."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "secret" / "s.jpg")
    fixtures.make_jpeg(photos / "open" / "o.jpg")
    (photos / "secret" / ".album.toml").write_text('allow = ["boss"]\n', encoding="utf-8")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        '[links]\n"Secret" = "secret"\n"Open" = "open"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    conn, ix = index_for(cfg)
    trips = ix.album("trips", admin())
    boss = queries.Viewer(token="boss", name="Boss")
    sis = queries.Viewer(token="sis", name="Sis")
    assert [c.title for c in ix.linked_albums(trips, boss)] == ["Secret", "Open"]
    assert [c.title for c in ix.linked_albums(trips, sis)] == ["Open"]
    conn.close()


# ------------------------------------------------------------ the config file

def test_a_bad_link_is_reported_and_the_rest_of_the_file_survives(tmp_path):
    """One mistyped path must not cost the album its title."""
    (tmp_path / ".album.toml").write_text(
        'title = "Trips"\n[links]\n'
        '"Good" = "2026/07/thailand"\n'
        '"Escape" = "../../etc"\n'
        '"Wrong" = 5\n', encoding="utf-8")
    cfg = album.load(tmp_path)
    assert cfg.title == "Trips"
    assert cfg.links == (("Good", "2026/07/thailand"),)
    assert any("Escape" in e for e in cfg.errors)
    assert any("Wrong" in e for e in cfg.errors)


def test_a_leading_or_trailing_slash_is_tolerated(tmp_path):
    (tmp_path / ".album.toml").write_text(
        '[links]\n"T" = "/2026/07/thailand/"\n', encoding="utf-8")
    assert album.load(tmp_path).links == (("T", "2026/07/thailand"),)


def test_a_card_with_nothing_to_count_says_nothing(tmp_path):
    """A directory holding only subdirectories of links counts none of its
    own -- links are counted where they are written and never summed upwards --
    and "0 photos" under its card is worse than no caption at all."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "real" / "a.jpg")
    (photos / "collections" / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "collections" / "trips" / ".album.toml").write_text(
        '[links]\n"Real" = "real"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    conn, ix = index_for(cfg)
    outer = ix.album("collections", admin())
    assert outer.n_photos_shown == 0            # nothing of its own, nothing linked
    conn.close()

    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/").get_data(as_text=True)
    assert "collections" in body.lower()        # the card is there
    assert "0 photos" not in body               # without a pointless count


def test_check_names_a_dead_link_and_leaves_a_links_album_off_the_empty_list(tmp_path):
    """A dead link is dropped silently when a page is drawn, which is right for
    a visitor and useless for whoever has to work out why a card vanished.
    `check` is where it surfaces. The same album must not then be reported as an
    empty directory: it has no photographs of its own and is not scratch space.
    """
    from harelphotos import check as check_mod

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "2026" / "07" / "thailand" / "a.jpg")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        '[links]\n'
        '"Thailand" = "2026/07/thailand"\n'
        '"Greece" = "2019/07/naxos"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)

    r = check_mod.run(cfg, conn)
    conn.close()

    assert r.dead_links == [("trips", "Greece", "2019/07/naxos")]
    assert r.problems >= 1                      # and it counts as a problem
    assert "trips" not in r.empty_dirs          # not a scratch directory


# ------------------------------------------------------------------- covers

def test_a_links_album_has_no_cover_of_its_own(tree):
    """Deliberately, and worth an assertion so it is a decision rather than an
    accident: a links album shows the placeholder card.

    Borrowing the first target's cover was considered. It puts one trip's
    photograph on a card labelled "Trips", which is rarely the one anybody
    would have chosen, and there is no way to overrule it without inventing the
    very setting below.
    """
    conn, ix = index_for(tree)
    assert ix.cover_photo(ix.album("trips", admin()), admin()) is None
    conn.close()


def test_a_cover_can_name_a_photograph_from_the_top_of_the_tree(tmp_path):
    """The escape hatch for an album with no photographs beneath it, where
    every relative path names something that does not exist."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "2026" / "07" / "thailand" / "a.jpg")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        'cover = "/2026/07/thailand/a.jpg"\n'
        '[links]\n"Thailand" = "2026/07/thailand"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    conn, ix = index_for(cfg)
    cover = ix.cover_photo(ix.album("trips", admin()), admin())
    assert cover is not None and cover.name == "a.jpg"
    conn.close()

    # And it reaches the page, rather than resolving and then being dropped.
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/").get_data(as_text=True)
    assert "card-blank" not in body


def test_a_cover_from_elsewhere_cannot_show_what_a_viewer_may_not_see(tmp_path):
    """A cover reaching across the tree is a new way to point at a restricted
    album, so the permission check has to be the target's own. Otherwise
    anyone could put someone else's private photograph on a public card."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "private" / "secret.jpg")
    (photos / "private" / ".album.toml").write_text(
        'allow = ["nyh"]\n', encoding="utf-8")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        'cover = "/private/secret.jpg"\n'
        '[links]\n"Private" = "private"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    conn, ix = index_for(cfg)
    stranger = queries.Viewer(token="nobody", name="nobody", is_admin=False)
    assert ix.cover_photo(ix.album("trips", stranger), stranger) is None
    assert ix.cover_photo(ix.album("trips", admin()), admin()).name == "secret.jpg"
    conn.close()


def test_a_cover_path_using_dot_dot_is_reported_rather_than_silently_ignored(tmp_path):
    (tmp_path / ".album.toml").write_text(
        'cover = "../2026/a.jpg"\n', encoding="utf-8")
    cfg = album.load(tmp_path)
    assert cfg.cover == "auto"
    assert any("'/'" in e for e in cfg.errors)


def test_the_cover_command_accepts_a_path_from_the_top_of_the_tree(tmp_path, capsys):
    """The same syntax as `.album.toml`, because an album with no photographs
    beneath it cannot be given a cover any other way."""
    from harelphotos import cli

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "2026" / "07" / "thailand" / "a.jpg")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        '[links]\n"Thailand" = "2026/07/thailand"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'photo_root = "{photos}"\n'
        f'derived_root = "{tmp_path / "state" / "derived"}"\n'
        f'index_db = "{cfg.index_db}"\n'
        f'users_file = "{tmp_path / "users.toml"}"\n'
        f'secret_key_file = "{tmp_path / "secret_key"}"\n', encoding="utf-8")

    assert cli.main(["-c", str(cfg_path), "cover", "trips",
                     "/2026/07/thailand/a.jpg"]) == 0
    assert "showing: a.jpg" in capsys.readouterr().out

    # Stored with the slash, so it still means "from the top" when read back.
    conn, ix = index_for(cfg)
    assert ix.cover_photo(ix.album("trips", admin()), admin()).name == "a.jpg"
    conn.close()

    # And a relative path still means relative: this one does not exist.
    assert cli.main(["-c", str(cfg_path), "cover", "trips",
                     "2026/07/thailand/a.jpg"]) == 1


def test_an_index_the_server_cannot_read_is_a_plain_page_not_a_500(tmp_path):
    """The failure mode of the upgrade that introduced all this.

    The index is opened per request, so an index the running code does not
    understand used to mean an internal server error on every page, with the
    reason only in the traceback. It should say what is happening instead --
    without the database's path, since this renders before the login gate.
    """
    import sqlite3

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "2019" / "a.jpg")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    # What a server looks like when it was not restarted after an upgrade.
    c = sqlite3.connect(cfg.index_db)
    c.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
              (str(db.SCHEMA_VERSION + 1),))
    c.commit()
    c.close()

    app = create_app(cfg, require_login=True)
    app.config.update(TESTING=True)
    r = app.test_client().get("/a/")
    body = r.get_data(as_text=True)

    assert r.status_code == 503                  # not 500: "not now", not "broken"
    assert "Restart it" in body
    assert str(cfg.index_db) not in body         # no filesystem path on the page


def test_the_placeholder_card_is_drawn_not_written(tmp_path):
    """It used to be the character U+1F5C0 FOLDER, which a desktop has a font
    for and a phone does not -- Android drew the missing-character box on every
    album without a cover. The bug was invisible on the machine it was written
    on, which is the reason for a test rather than a comment.
    """
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "2026" / "a.jpg")
    (photos / "trips").mkdir(parents=True, exist_ok=True)
    (photos / "trips" / ".album.toml").write_text(
        '[links]\n"X" = "2026"\n', encoding="utf-8")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()

    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/").get_data(as_text=True)

    assert "card-blank" in body
    assert "<svg" in body.split("card-blank", 1)[1][:300]
    # Nothing on the page depends on a font having an astral-plane glyph.
    assert [c for c in body if ord(c) > 0xFFFF] == []
