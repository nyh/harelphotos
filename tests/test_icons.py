"""The album's icon: `[ui] icon` and the three places it is used.

Every assertion here stands for a bug that was in the code, because this is
mostly image plumbing whose failures are invisible until someone looks at a
phone: an icon whose transparency had been replaced by black, a PNG served as
`image/jpeg` to a browser told not to sniff, and a configured icon that could
never replace one already on disk.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from PIL import Image

from harelphotos import public_assets as pa, scanner
from harelphotos.web import create_app

from . import fixtures


def _logo(path, size=48, color=(231, 76, 60, 255)):
    """A square logo with transparent corners, like a real one."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(size):
        for x in range(size):
            # A blunt rounded rectangle: the corners stay fully transparent.
            if 4 <= x < size - 4 or 4 <= y < size - 4:
                im.putpixel((x, y), color)
    im.save(path)
    return path


@pytest.fixture
def site(tmp_path):
    """A scanned tree whose config names an icon."""
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    cfg = replace(cfg, ui=replace(cfg.ui, icon=_logo(tmp_path / "logo.png")))
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    return cfg


def _client(cfg):
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    return app.test_client()


def test_every_size_is_built(site):
    assert pa.build_icons(site)
    assert pa.icons(site) == list(pa.ICON_SIZES)
    for size in pa.ICON_SIZES:
        im = Image.open(pa.public_dir(site) / f"icon-{size}.png")
        assert im.size == (size, size)


def test_transparency_survives_except_for_ios(site):
    """The corners must stay transparent -- with one deliberate exception.

    `build_icons` used to convert to RGB before resizing, which discards the
    alpha channel and leaves the stored color behind. The stored color under
    a transparent corner is black, so a logo with rounded corners became an
    opaque black tile with a picture in the middle.

    The 180 is flattened onto white on purpose: iOS composites an
    apple-touch-icon onto black rather than honoring alpha, so a transparent
    one would come out as that same black tile on an iPhone's home screen.
    """
    pa.build_icons(site)
    for size in pa.ICON_SIZES:
        im = Image.open(pa.public_dir(site) / f"icon-{size}.png").convert("RGBA")
        corner = im.load()[1, 1]
        if size in pa.OPAQUE_ICON_SIZES:
            assert corner == (*pa.OPAQUE_ICON_BACKGROUND, 255)
        else:
            assert corner[3] == 0, f"icon-{size}.png lost its transparency"


def test_changing_the_icon_rebuilds_it(site, tmp_path):
    """Editing the config has to be enough.

    Every size is skipped when the file already exists, which is right for a
    restart and wrong for a changed setting: without the stamp recording what
    the icons were made from, a new `[ui] icon` was configured, the server
    restarted, and the old icon stayed for ever.
    """
    pa.build_icons(site)
    assert pa.build_icons(site) == []                  # nothing to redo

    other = _logo(tmp_path / "blue.png", color=(0, 128, 255, 255))
    changed = replace(site, ui=replace(site.ui, icon=other))
    assert pa.build_icons(changed)                     # noticed
    middle = Image.open(pa.public_dir(changed) / "icon-192.png").convert("RGBA")
    assert middle.load()[96, 96] == (0, 128, 255, 255)


def test_app_icon_is_still_accepted_under_its_old_name(tmp_path):
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    cfg = replace(cfg, ui=replace(cfg.ui,
                                  app_icon=_logo(tmp_path / "old.png")))
    assert pa.icon_source(cfg) == cfg.ui.app_icon
    assert pa.build_icons(cfg)


def test_icon_beats_app_icon_and_the_hero(tmp_path):
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    cfg = replace(cfg, ui=replace(
        cfg.ui,
        icon=_logo(tmp_path / "new.png"),
        app_icon=_logo(tmp_path / "old.png"),
        landing_image=_logo(tmp_path / "hero.png"),
    ))
    assert pa.icon_source(cfg) == cfg.ui.icon


def test_served_as_png_because_of_nosniff(site):
    """Every response carries `X-Content-Type-Options: nosniff`.

    The route used to answer "AVIF, or else JPEG" from the file name, so the
    PNG icons went out labelled `image/jpeg` -- which nosniff turns from a
    cosmetic error into a browser refusing to render them at all.
    """
    pa.build_icons(site)
    c = _client(site)
    for size in pa.ICON_SIZES:
        r = c.get(f"/public/icon-{size}.png")
        assert r.status_code == 200
        assert r.headers["Content-Type"] == "image/png"


def test_only_a_versioned_url_may_be_frozen(site):
    """`icon-192.png` is a fixed name whose contents change with the config.

    Freezing it for a year was a promise we could not keep, and browsers kept
    it for us: a phone that had installed the site went on offering the icon it
    first downloaded. Adding `?v=<tag>` makes the promise true for that exact
    URL, so it may be frozen -- while a request with no tag, which is somebody's
    old bookmark, must not be.
    """
    pa.build_icons(site)
    c = _client(site)
    tag = pa.asset_tag(site)

    frozen = c.get(f"/public/icon-192.png?v={tag}").headers["Cache-Control"]
    assert "immutable" in frozen and "31536000" in frozen

    plain = c.get("/public/icon-192.png").headers["Cache-Control"]
    assert "immutable" not in plain and "31536000" not in plain


def test_changing_the_icon_changes_every_url(site, tmp_path):
    """What actually reaches a phone that already has the old one.

    Shortening the cache lifetime was not enough on its own: Android installs
    a site by having a small APK minted with the icon inside it, and both that
    and the browser went on showing an icon already replaced on the server.
    A URL nobody has ever seen cannot be stale, and a manifest whose contents
    changed is also what prompts Android to mint the app again.
    """
    pa.build_icons(site)
    before = pa.asset_tag(site)
    assert f"?v={before}" in _client(site).get("/a/").get_data(as_text=True)

    changed = replace(site, ui=replace(
        site.ui, icon=_logo(tmp_path / "blue.png", color=(0, 128, 255, 255))))
    pa.build_icons(changed)
    after = pa.asset_tag(changed)
    assert after != before

    m = json.loads(_client(changed).get(
        "/manifest.webmanifest").get_data(as_text=True))
    assert all(f"?v={after}" in i["src"] for i in m["icons"])


def test_the_manifest_lists_only_installable_sizes(site):
    """A 32px favicon has no business on a home screen."""
    pa.build_icons(site)
    c = _client(site)
    m = json.loads(c.get("/manifest.webmanifest").get_data(as_text=True))
    sizes = [i["sizes"] for i in m["icons"]]
    assert "32x32" not in sizes
    assert "192x192" in sizes


def test_the_front_page_wears_the_icon(site):
    """Beside the heading, and as the tab icon on every page."""
    pa.build_icons(site)
    c = _client(site)
    front = c.get("/a/").get_data(as_text=True)
    assert 'class="masthead-icon"' in front
    assert 'rel="icon"' in front and "/public/icon-32.png" in front
    assert 'rel="apple-touch-icon"' in front

    # Not beside a heading there is none of, but the tab icon stays.
    inner = c.get("/a/2019/01/").get_data(as_text=True)
    assert "masthead-icon" not in inner
    assert "/public/icon-32.png" in inner


def test_no_icon_configured_means_no_links_and_no_manifest_icons(tmp_path):
    """A browser is happy with an absent icon and unhappy with a broken one."""
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    cfg = replace(cfg, ui=replace(cfg.ui, icon=None, app_icon=None,
                                  landing_image=None))
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    assert pa.icon_source(cfg) is None
    assert pa.build_icons(cfg) == []

    c = _client(cfg)
    front = c.get("/a/").get_data(as_text=True)
    assert 'rel="icon"' not in front
    assert "masthead-icon" not in front
    m = json.loads(c.get("/manifest.webmanifest").get_data(as_text=True))
    assert "icons" not in m
