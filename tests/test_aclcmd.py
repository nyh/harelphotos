"""``harelphotos acl`` (DESIGN.md 6, 15).

The command edits files that decide who can see private photographs, so the
tests here are less about the happy path than about the two ways it could do
harm: writing a restriction that is not actually in force, and damaging a
`.album.toml` somebody wrote by hand.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pytest

from harelphotos import aclcmd, db, scanner
from harelphotos.web import create_app

from . import fixtures


@pytest.fixture
def project(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "trip" / "a.jpg", size=(120, 90))
    fixtures.make_jpeg(photos / "trip" / "private" / "secret.jpg", size=(120, 90))
    cfg = fixtures.make_config(tmp_path, photos)
    object.__setattr__(cfg, "groups", {"family": ("nyh", "sis")})
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    for name in ("nyh", "sis", "cousin"):
        fixtures.add_user(cfg, name, "pw")
    return cfg


def rescan(cfg, subpath=""):
    conn = db.open_index(cfg.index_db)
    scanner.scan(cfg, conn, subpath=subpath, headers_only=True)
    conn.close()


def test_an_unrestricted_directory_reports_as_such(project):
    assert aclcmd.chain_with_sources(project, "trip") == []


def test_a_restriction_names_the_directory_it_came_from(project):
    aclcmd.write_allow(project, "trip", ["nyh", "sis"])
    aclcmd.write_allow(project, "trip/private", ["@family"])

    links = aclcmd.chain_with_sources(project, "trip/private")
    assert [(l.dir_path, l.allow) for l in links] == [
        ("trip", ("nyh", "sis")),
        ("trip/private", ("@family",)),
    ]


def test_restrictions_accumulate_rather_than_override(project):
    """The rule the whole design turns on: a subdirectory can only ever narrow
    what its parent allowed, never widen it."""
    aclcmd.write_allow(project, "trip", ["nyh"])
    aclcmd.write_allow(project, "trip/private", ["sis"])
    links = aclcmd.chain_with_sources(project, "trip/private")
    can, _ = aclcmd.who_can_view(project, links)
    # sis is named on the inner list but not the outer one, so she is out.
    assert can == set()


def test_allow_replace_discards_what_was_inherited(project):
    aclcmd.write_allow(project, "trip", ["nyh"])
    aclcmd.write_allow(project, "trip/private", ["sis"], replace=True)
    links = aclcmd.chain_with_sources(project, "trip/private")
    assert [l.dir_path for l in links] == ["trip/private"]
    can, _ = aclcmd.who_can_view(project, links)
    assert can == {"sis"}


def test_a_name_matching_nobody_is_reported(project):
    """It fails silently and always in the restrictive direction: the album
    shows to fewer people than intended and nobody complains, because nobody
    complains about photos they cannot see."""
    aclcmd.write_allow(project, "trip", ["nyh", "granny", "@nosuchgroup"])
    links = aclcmd.chain_with_sources(project, "trip")
    can, unknown = aclcmd.who_can_view(project, links)
    assert unknown == {"granny", "@nosuchgroup"}
    assert can == {"nyh"}


def test_groups_are_expanded(project):
    aclcmd.write_allow(project, "trip", ["@family"])
    can, unknown = aclcmd.who_can_view(project, aclcmd.chain_with_sources(project, "trip"))
    assert can == {"nyh", "sis"}
    assert not unknown


def test_clearing_removes_the_restriction(project):
    aclcmd.write_allow(project, "trip", ["nyh"])
    aclcmd.write_allow(project, "trip", None)
    assert aclcmd.chain_with_sources(project, "trip") == []


# ------------------------------------------- never writing to the photo tree

HANDWRITTEN = '''\
# Our big trip. Do not reorder these!
title = "The Trip"
allow = ["nyh"]
cover = "a.jpg"
'''


def test_nothing_is_written_into_the_photo_tree(project):
    """The photo tree is read-only to this software.

    Not merely by convention: the systemd unit sets ProtectHome=read-only and
    photos usually live under a home directory, so a write here would fail with
    EROFS on a real server while working on a developer's machine.
    """
    before = {p: p.stat().st_mtime_ns
              for p in project.photo_root.rglob("*") if p.is_file()}

    aclcmd.write_allow(project, "trip", ["nyh", "sis"])

    after = {p: p.stat().st_mtime_ns
             for p in project.photo_root.rglob("*") if p.is_file()}
    assert after == before, "the photo tree was modified"
    assert project.overrides_file.is_file()


def test_a_hand_written_album_file_still_works_and_is_untouched(project):
    """`.album.toml` remains the place to hand-write anything; the override
    layer only takes precedence over the keys it names."""
    from harelphotos import album

    path = project.photo_root / "trip" / album.ALBUM_FILE
    path.write_text(HANDWRITTEN, encoding="utf-8")

    # The hand-written restriction is honoured with no override present.
    links = aclcmd.chain_with_sources(project, "trip")
    assert [l.allow for l in links] == [("nyh",)]

    # An override wins ...
    aclcmd.write_allow(project, "trip", ["sis"])
    links = aclcmd.chain_with_sources(project, "trip")
    assert [l.allow for l in links] == [("sis",)]

    # ... without the file on disk changing at all ...
    assert path.read_text(encoding="utf-8") == HANDWRITTEN

    # ... and clearing the override brings the hand-written one back.
    aclcmd.write_allow(project, "trip", None)
    links = aclcmd.chain_with_sources(project, "trip")
    assert [l.allow for l in links] == [("nyh",)]


def test_the_overrides_file_survives_other_entries(project):
    aclcmd.write_allow(project, "trip", ["nyh"])
    aclcmd.write_allow(project, "trip/private", ["sis"])
    aclcmd.write_allow(project, "trip", ["nyh", "sis"])

    from harelphotos import overrides

    table = overrides.load(project)
    assert table["trip"].allow == ("nyh", "sis")
    assert table["trip/private"].allow == ("sis",)


def test_a_damaged_overrides_file_is_ignored_not_fatal(project):
    """Read on every request; a syntax error must not take the site down. The
    restrictions that actually guard a request live in the index."""
    project.overrides_file.write_text('["trip"\nallow = ', encoding="utf-8")
    assert aclcmd.chain_with_sources(project, "trip") == []


def test_a_missing_directory_is_an_error(project):
    with pytest.raises(aclcmd.AclError, match="not a directory"):
        aclcmd.write_allow(project, "nope/nowhere", ["nyh"])


# --------------------------------------------------------- actually in force

def test_a_written_restriction_is_enforced_after_a_rescan(project):
    """The failure that matters. The web process reads the chain computed at
    scan time, so a restriction that is only in the file is not in force."""
    aclcmd.write_allow(project, "trip/private", ["nyh"])
    rescan(project, "trip/private")

    app = create_app(project, require_login=True)
    app.config.update(TESTING=True)

    def as_user(name):
        import re

        c = app.test_client()
        page = c.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        c.post("/login", data={"username": name, "password": "pw", "csrf": token})
        return c

    assert as_user("nyh").get("/a/trip/private/").status_code == 200
    for other in ("sis", "cousin"):
        c = as_user(other)
        assert c.get("/a/trip/private/").status_code == 404, other
        # And the photo itself, not merely the listing that links to it.
        assert c.get("/p/trip/private/secret.jpg").status_code == 404, other


def test_restricted_dirs_lists_what_the_index_believes(project):
    aclcmd.write_allow(project, "trip/private", ["nyh"])
    rescan(project, "trip/private")
    conn = db.open_index(project.index_db, read_only=True)
    rows = aclcmd.restricted_dirs(conn)
    conn.close()
    assert [p for p, _ in rows] == ["trip/private"]
