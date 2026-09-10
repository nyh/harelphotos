"""The web application (DESIGN.md 10, 11)."""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from dataclasses import replace

import pytest

from harelphotos import db, scanner
from harelphotos.web import create_app

from . import fixtures


@pytest.fixture
def scanned(tmp_path):
    """A scanned fixture tree and its config."""
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    return cfg


@pytest.fixture
def client(scanned):
    """The default: no login required, as `harelphotos serve` runs locally."""
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.harelphotos_cfg = scanned
        c.harelphotos_app = app
        yield c


def test_root_redirects_to_the_top_album(client):
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["Location"] == "/a/"       # not "/a//"


def test_top_album_lists_subalbums(client):
    r = client.get("/a/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Twenty Nineteen" in body          # title from .album.toml
    assert "Scans" in body
    assert "photo" in body


def test_nested_album(client):
    r = client.get("/a/2019/01/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "a.jpg" in body                    # alt text
    assert "/p/2019/01/a.jpg" in body         # links to the photo page


def test_site_name_heads_the_front_page_only(client):
    """The site is named as a heading on the front page and nowhere else.

    On every album page it read as a letterhead: above a listing of January
    2019 the largest text on the screen named the site rather than what you
    had opened, and it repeated all the way down the tree. Deeper pages carry
    the name in the breadcrumb and the window title instead, and the
    single-photo page exists to show one photograph as large as it will go.
    """
    heading = f'<h1 class="masthead">{client.harelphotos_cfg.ui.heading}</h1>'
    assert heading in client.get("/a/").get_data(as_text=True)
    assert "masthead" not in client.get("/a/2019/01/").get_data(as_text=True)
    assert "masthead" not in client.get("/p/2019/01/a.jpg").get_data(
        as_text=True)


def test_front_page_heading_is_heading_not_site_title(scanned):
    """`[ui] heading` is the visible heading, `[ui] site_title` the name in
    the chrome -- the window title, the phone's home screen, the "X — site"
    suffixes.

    They default to the same string, which is exactly why this needs its own
    test: the masthead was written against `site_title` and every other test
    passed, while a real config that set the two differently -- as the login
    page has always invited, since its own h1 is `heading` -- put the wrong
    words at the top of the page.
    """
    cfg = replace(scanned, ui=replace(scanned.ui,
                                      heading="The Family Photo Album",
                                      site_title="fam-pics"))
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        body = c.get("/a/").get_data(as_text=True)
    assert '<h1 class="masthead">The Family Photo Album</h1>' in body
    assert "fam-pics</title>" in body            # site_title still names the tab


def test_breadcrumbs_are_present(client):
    body = client.get("/a/2019/01/").get_data(as_text=True)
    assert 'href="/a/"' in body
    assert 'href="/a/2019/"' in body


def test_directory_with_no_photos_beneath_is_not_listed(client):
    """`movies/` holds only a .mp4, so it is not an album."""
    body = client.get("/a/").get_data(as_text=True)
    assert "/a/movies/" not in body


def test_no_headings_when_an_album_has_only_one_kind_of_thing(client):
    # 2019/ has subdirectories and no loose photos.
    body = client.get("/a/2019/").get_data(as_text=True)
    assert '<h2 class="section">' not in body
    # A leaf album has photos and no subdirectories.
    body = client.get("/a/2019/01/").get_data(as_text=True)
    assert '<h2 class="section">' not in body


def test_both_headings_when_an_album_has_both(tmp_path):
    """Sections are labelled only when there is more than one kind."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "mixed" / "loose.jpg")
    fixtures.make_jpeg(photos / "mixed" / "sub" / "inner.jpg")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/mixed/").get_data(as_text=True)
    assert '<h2 class="section">Albums</h2>' in body
    assert '<h2 class="section">Photos</h2>' in body


def test_missing_album_is_404(client):
    assert client.get("/a/nope/").status_code == 404
    assert client.get("/a/2019/99/").status_code == 404


def test_path_traversal_is_refused(client):
    for bad in ("/a/../../etc/", "/a/2019/../../", "/p/../../etc/passwd"):
        assert client.get(bad).status_code in (301, 308, 404), bad
    # A DB lookup by exact path means even a well-formed attempt finds nothing.
    assert client.get("/i/256/../../etc/passwd").status_code in (301, 308, 404)


def test_photo_page(client):
    r = client.get("/p/2019/01/a.jpg")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Download original" in body
    assert "/orig/2019/01/a.jpg" in body
    assert "15 January 2019" in body           # EXIF date, rendered as recorded


def test_photo_page_has_prev_next(client):
    body = client.get("/p/2019/01/b.jpg").get_data(as_text=True)
    assert "/p/2019/01/a.jpg" in body          # previous, by date order


def test_photo_with_no_exif_date_is_labelled_differently(client):
    body = client.get("/p/2019/02/d.jpg").get_data(as_text=True)
    assert "File date" in body
    assert "no date recorded" in body


def test_photo_info_shows_place_from_album_location(client):
    # ancient/.album.toml sets location; the photo itself has no GPS.
    body = client.get("/p/ancient/old.jpg").get_data(as_text=True)
    assert "Haifa, Israel" in body


def test_missing_photo_is_404(client):
    assert client.get("/p/2019/01/ghost.jpg").status_code == 404


# ------------------------------------------------------------------ images

def test_image_tier_is_served(client):
    r = client.get("/i/512/2019/01/a.jpg", headers={"Accept": "image/avif,image/webp,*/*"})
    assert r.status_code == 200
    assert r.mimetype == "image/avif"
    assert "immutable" in r.headers["Cache-Control"]


def test_vary_accept_is_always_set(client):
    """One URL returns three formats; without Vary a cache will serve the
    wrong one, silently, for a year."""
    r = client.get("/i/512/2019/01/a.jpg", headers={"Accept": "image/avif"})
    assert "Accept" in r.headers.get("Vary", "")


def test_unconfigured_tier_is_404(client):
    assert client.get("/i/999/2019/01/a.jpg").status_code == 404


def test_webp_fallback_for_a_client_without_avif(client):
    r = client.get("/i/512/2019/01/a.jpg", headers={"Accept": "image/webp,*/*"})
    assert r.status_code == 200
    assert r.mimetype == "image/webp"


def test_jpeg_fallback_for_an_ancient_client(client):
    r = client.get("/i/512/2019/01/a.jpg", headers={"Accept": "*/*"})
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"


def test_download_original_is_an_attachment(client):
    r = client.get("/orig/2019/01/a.jpg")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    assert "a.jpg" in r.headers["Content-Disposition"]


def test_inline_original_is_not_an_attachment(client):
    """For a photo smaller than every tier: it must display, not download."""
    r = client.get("/i/orig/2019/01/a.jpg")
    assert r.status_code == 200
    assert "attachment" not in r.headers.get("Content-Disposition", "")


def test_srcset_uses_real_per_photo_widths(client):
    """A portrait photo in the 512 tier is 384 wide; advertising 512 would
    make the browser's DPR arithmetic wrong for half the collection."""
    body = client.get("/a/2019/01/").get_data(as_text=True)
    # The 800x600 fixtures give 512->512w and 256->256w on the long edge.
    assert "512w" in body
    assert "256w" in body


def test_no_srcset_entry_for_a_tier_that_was_never_generated(client):
    body = client.get("/a/2019/01/").get_data(as_text=True)
    # 800x600 sources: 2048 is far larger than the original and was never
    # generated, so it must not be advertised or the browser fetches a 404.
    assert "/i/2048/" not in body


def test_grid_offers_the_larger_tiers_too(client):
    """A wide tile in a justified row can need more than 512 px.

    On a retina screen a 3:1 panorama at a 180 px row height wants 1080
    device px, so a ladder stopping at the thumbnail tiers would upscale it.
    The larger tiers already exist and `sizes` stops a small tile fetching one.
    """
    body = client.get("/a/2019/01/").get_data(as_text=True)
    assert "/i/1280/" in body        # 800x600 fixtures have a 1280 tier


# ------------------------------------------------------------------- misc

def test_security_headers(client):
    r = client.get("/a/")
    assert "no-referrer" in r.headers["Referrer-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_robots_noindex(client):
    assert "noindex" in client.get("/a/").get_data(as_text=True)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["photos"] == 9


def test_static_assets_are_served(client):
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_grid_carries_aspect_ratios_for_the_layout(client):
    body = client.get("/a/2019/01/").get_data(as_text=True)
    assert 'data-ar="1.3333"' in body        # 800x600


# ------------------------------------------------- serving during a scan

def test_pages_are_served_while_a_scan_holds_a_write_transaction(client, tmp_path):
    """A scan must never take the site down (DESIGN.md 8).

    WAL is what makes this work: readers do not block on a writer, and the
    application's connection is read-only and per-request.
    """
    import sqlite3

    cfg = client.harelphotos_cfg
    writer = sqlite3.connect(cfg.index_db)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE photos SET title = 'mid-scan'")
        # Uncommitted: the reader must still work, and must see the old value.
        assert client.get("/a/2019/01/").status_code == 200
        assert client.get("/p/2019/01/a.jpg").status_code == 200
        assert "mid-scan" not in client.get("/a/2019/01/").get_data(as_text=True)
        writer.rollback()
    finally:
        writer.close()


def test_a_full_rescan_alongside_requests_never_fails_one(client):
    """Interleave a real scan with real requests, in one process."""
    import threading

    from harelphotos import db, scanner

    cfg = client.harelphotos_cfg
    conn = db.open_index(cfg.index_db)
    conn.execute("UPDATE photos SET hdr_stale = 1, deriv_key = NULL")
    conn.commit()

    errors: list[str] = []
    stop = threading.Event()

    def hammer():
        # A Flask test client is bound to the thread that made it, so the
        # thread gets its own.
        own = client.harelphotos_app.test_client()
        while not stop.is_set():
            for url in ("/a/", "/a/2019/01/", "/p/2019/01/a.jpg", "/healthz"):
                r = own.get(url)
                if r.status_code != 200:
                    errors.append(f"{url} -> {r.status_code}")

    t = threading.Thread(target=hammer, daemon=True)
    t.start()
    try:
        stats = scanner.scan(cfg, conn, jobs=1)
    finally:
        stop.set()
        t.join(timeout=10)
        conn.close()

    assert stats.photos_derived > 0        # the scan really did work
    assert errors == []


def test_regenerating_an_image_never_leaves_it_missing(client):
    """Atomic writes mean the old file stays until the new one is complete."""
    from harelphotos import derive

    cfg = client.harelphotos_cfg
    before = client.get("/i/512/2019/01/a.jpg", headers={"Accept": "image/avif"})
    assert before.status_code == 200
    derive.derive(cfg.photo_root / "2019/01/a.jpg", "2019/01/a.jpg", cfg)
    after = client.get("/i/512/2019/01/a.jpg", headers={"Accept": "image/avif"})
    assert after.status_code == 200


def test_sizes_reflects_the_real_tile_width(client):
    """A flat `sizes` makes the browser upscale every non-square photo.

    Justified rows scale each tile to the row height, so a 3:1 panorama
    renders three times wider than a square one and needs a correspondingly
    bigger file.
    """
    body = client.get("/a/2019/01/").get_data(as_text=True)
    # 800x600 is 4:3, so 1.333 * 180 = 240px, not a flat 180px.
    assert "240px" in body
    assert "33vw, 180px" not in body


def test_a_pending_photo_does_not_put_its_original_in_the_grid(tmp_path):
    """The grid must never link an ungenerated original.

    Those can be tens of megabytes — one album of them would pull hundreds.
    Distinct from a photo that legitimately has no derivatives because it is
    smaller than every size, where the original is genuinely small.
    """
    from harelphotos import db, scanner

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a" / "done.jpg", size=(2000, 1500))
    fixtures.make_jpeg(photos / "a" / "waiting.jpg", size=(2000, 1500))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    # Put one photo back into the pending state, as an interrupted scan would.
    conn.execute("UPDATE photos SET deriv_key = NULL, deriv_tiers = NULL "
                 "WHERE name = 'waiting.jpg'")
    conn.commit()
    conn.close()

    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/a/").get_data(as_text=True)

    assert "/i/orig/a/waiting.jpg" not in body      # the whole point
    assert "image not generated yet" in body
    assert "/i/512/a/done.jpg" in body              # the finished one is fine


def test_a_photo_with_no_worthwhile_derivative_uses_its_original(tmp_path):
    """The case /i/orig/ exists for.

    A photo can end up with no derivatives legitimately: re-encoding something
    very small can produce a *larger* file, which is then discarded in favour
    of the original. The distinguishing mark is that deriv_key is set — the
    scan did look at it and decided — unlike a pending photo, where it is NULL.
    """
    from harelphotos import scanner

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "b" / "small.jpg", size=(200, 150))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.execute("UPDATE photos SET deriv_tiers = '[]'")     # nothing was worth keeping
    conn.commit()
    conn.close()

    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()
    body = c.get("/a/b/").get_data(as_text=True)
    assert "/i/orig/b/small.jpg" in body            # shown, not blanked
    assert "image not generated yet" not in body    # and not called pending
    assert c.get("/i/orig/b/small.jpg").status_code == 200


def test_a_filename_with_a_space_works_everywhere(client):
    """A space breaks `srcset`, where whitespace separates URL from descriptor.

    One photo called "zPic 4.jpg" rendered as a grey box because of this, and
    107 photos in the real collection have a space in the name.
    """
    import re

    body = client.get("/a/2019/02/").get_data(as_text=True)
    # Every candidate in every srcset must be free of raw spaces, or the
    # browser cannot parse the list at all.
    for srcset in re.findall(r'srcset="([^"]*)"', body):
        for candidate in srcset.split(","):
            url, _, descriptor = candidate.strip().rpartition(" ")
            assert " " not in url, f"raw space in srcset URL: {url!r}"
            assert descriptor.endswith("w"), candidate

    page = client.get("/p/2019/02/zPic 4.jpg")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "zPic%204.jpg" in html
    assert "zPic 4.jpg?v=" not in html          # never raw inside a URL

    # And the encoded URLs actually resolve.
    for url in ("/i/512/2019/02/zPic%204.jpg", "/i/orig/2019/02/zPic%204.jpg",
                "/orig/2019/02/zPic%204.jpg", "/p/2019/02/zPic%204.jpg"):
        assert client.get(url).status_code == 200, url


def test_album_urls_are_encoded_too(tmp_path):
    """Directory names can contain spaces just as easily as filenames."""
    from harelphotos import scanner

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a trip" / "x.jpg", size=(900, 700))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    app = create_app(cfg, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()
    body = c.get("/a/").get_data(as_text=True)
    assert 'href="/a/a%20trip/"' in body
    assert c.get("/a/a%20trip/").status_code == 200



def test_a_wrapped_bullet_keeps_its_whole_sentence(scanned, tmp_path, monkeypatch):
    """The privacy policy was being truncated mid-sentence.

    Every line of a bullet that did not itself start with "- " was dropped, so
    a bullet wrapped across two lines lost its second half -- in the document
    that is handed to Google as the site's privacy policy, and the kind of
    silent loss nobody notices by reading the source file.
    """
    from harelphotos.web import create_app

    src = tmp_path / "cfgdir"
    src.mkdir(parents=True, exist_ok=True)
    object.__setattr__(scanned, "source", src / "config.toml")
    (src / "privacy.md").write_text(
        "# Privacy\n\n"
        "- If you sign in with Google, Google tells us only your email address,\n"
        "  so that we can check it against the invitation list. Nothing else is\n"
        "  requested.\n"
        "- Cookies are used only to keep you signed in.\n",
        encoding="utf-8",
    )
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/privacy").get_data(as_text=True)

    assert "check it against the invitation list" in body
    assert "Nothing else is requested." in body
    assert "Cookies are used only to keep you signed in." in body
    # Two bullets, not four or one.
    assert body.count("<li>") == 2


def test_the_shipped_privacy_text_survives_rendering(scanned, tmp_path):
    """The template init actually writes, end to end -- it has wrapped bullets."""
    from harelphotos import initialise
    from harelphotos.web import create_app

    src = tmp_path / "cfgdir2"
    src.mkdir(parents=True, exist_ok=True)
    object.__setattr__(scanned, "source", src / "config.toml")
    (src / "privacy.md").write_text(initialise.PRIVACY_TEMPLATE, encoding="utf-8")

    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/privacy").get_data(as_text=True)

    # No bullet may end where a line break happened to fall.
    for tail in ("so that", "They do not record", "and", "including"):
        assert f"{tail}</li>" not in body, f"a bullet was truncated at {tail!r}"
    assert "nothing is sent to Google about what you view" in body
    assert "which photos anyone looked at" in body


def test_an_album_shows_its_location(scanned, tmp_path):
    """`location` in .album.toml already stands in for a place on photos with
    no GPS; the album it belongs to should say it too."""
    from harelphotos import scanner
    from harelphotos.web import create_app

    (scanned.photo_root / "2019" / "01" / ".album.toml").write_text(
        'title = "Twenty Nineteen"\nlocation = "Tel Aviv, Israel"\n', encoding="utf-8"
    )
    conn = db.open_index(scanned.index_db)
    scanner.scan(scanned, conn)
    conn.close()

    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/2019/01/").get_data(as_text=True)
    assert "Tel Aviv, Israel" in body


def test_a_location_is_hidden_when_places_are_switched_off(scanned, tmp_path):
    """show_gps = false means "do not tell people where this was", and a
    hand-written album location is exactly that."""
    from harelphotos import scanner
    from harelphotos.web import create_app

    (scanned.photo_root / "2019" / "01" / ".album.toml").write_text(
        'title = "Twenty Nineteen"\nlocation = "Tel Aviv, Israel"\n', encoding="utf-8"
    )
    conn = db.open_index(scanned.index_db)
    scanner.scan(scanned, conn)
    conn.close()

    object.__setattr__(scanned.ui, "show_gps", False)
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/2019/01/").get_data(as_text=True)
    assert "Tel Aviv" not in body


def test_the_photo_page_shows_date_and_place_without_opening_the_panel(client):
    """Where and when is what you want while looking at a photo; camera and
    exposure are for when you go looking. Before this, all of it was hidden
    behind the Info button and most people would never find any of it."""
    body = client.get("/p/2019/01/a.jpg").get_data(as_text=True)
    head, _, panel = body.partition('<aside id="info"')
    assert "titleline" in head
    # The date is in the always-visible part, not only inside the panel.
    assert "2019" in head.split('class="meta photo-meta"')[1][:200]


def test_the_place_respects_show_gps(scanned):
    from harelphotos.web import create_app

    object.__setattr__(scanned.ui, "show_gps", False)
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/p/2019/01/a.jpg").get_data(as_text=True)
    head, _, _ = body.partition('<aside id="info"')
    assert "photo-meta" not in head or "·" not in head.split("photo-meta")[1][:200]


def test_day_date_has_no_weekday_or_time(scanned):
    """It sits next to a filename, so it must stay short."""
    from harelphotos.web import create_app

    app = create_app(scanned, require_login=False)
    with app.app_context():
        f = app.jinja_env.filters["day_date"]
        out = f(1_565_000_000)
        assert out and "," not in out and ":" not in out
        assert f(None) == ""


# ------------------------------------------------------ installable web app

def test_the_manifest_is_reachable_without_a_session(scanned):
    """The browser fetches it before anyone has signed in, and it holds
    nothing but the site's name and icons."""
    from harelphotos.web import create_app

    app = create_app(scanned, require_login=True)
    app.config.update(TESTING=True)
    r = app.test_client().get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.mimetype == "application/manifest+json"


def test_the_manifest_asks_for_standalone_display(scanned, client):
    """The entire point: launched from the home screen there is no URL bar,
    which on a phone held sideways is a sixth of the screen."""
    import json as _json

    data = _json.loads(client.get("/manifest.webmanifest").get_data(as_text=True))
    assert data["display"] == "standalone"
    assert data["name"] == scanned.ui.site_title
    # "/" so the icon lands on the albums when signed in and the login page
    # when not, rather than a redirect either way.
    assert data["start_url"] == "/"


def test_no_icons_key_at_all_when_there_is_no_image(scanned, client):
    """An empty list is a manifest error; an absent key just lets the browser
    choose something."""
    import json as _json

    data = _json.loads(client.get("/manifest.webmanifest").get_data(as_text=True))
    assert "icons" not in data


def test_icons_are_square_pngs_cut_from_the_configured_image(scanned, tmp_path):
    from PIL import Image

    from harelphotos import public_assets

    src = tmp_path / "icon-source.jpg"
    Image.new("RGB", (1200, 800), (10, 120, 200)).save(src)
    object.__setattr__(scanned.ui, "app_icon", src)

    written = public_assets.build_icons(scanned)
    assert written, "no icons were produced"
    for size in public_assets.ICON_SIZES:
        p = public_assets.public_dir(scanned) / f"icon-{size}.png"
        with Image.open(p) as im:
            # Square and PNG: every platform accepts PNG for an installed app,
            # which is the one place AVIF is not the answer.
            assert im.size == (size, size), (size, im.size)
            assert im.format == "PNG"


def test_app_icon_overrides_the_landing_image(scanned, tmp_path):
    from PIL import Image

    from harelphotos import public_assets

    landing = tmp_path / "landing.jpg"
    icon = tmp_path / "icon.jpg"
    Image.new("RGB", (900, 600), (255, 0, 0)).save(landing)
    Image.new("RGB", (900, 600), (0, 255, 0)).save(icon)
    object.__setattr__(scanned.ui, "landing_image", landing)
    object.__setattr__(scanned.ui, "app_icon", icon)

    public_assets.build_icons(scanned, force=True)
    with Image.open(public_assets.public_dir(scanned) / "icon-192.png") as im:
        r, g, b = im.convert("RGB").getpixel((96, 96))
        assert g > 200 and r < 60, f"used the landing image, not app_icon: {(r, g, b)}"


def test_a_missing_icon_source_is_a_warning_not_a_crash(scanned, tmp_path):
    from harelphotos import public_assets

    object.__setattr__(scanned.ui, "app_icon", tmp_path / "nope.jpg")
    assert public_assets.build_icons(scanned) == []


def test_the_landing_image_keeps_its_aspect_ratio(scanned, tmp_path):
    """Never squashed to a fixed shape. A panorama makes any damage obvious."""
    from PIL import Image

    from harelphotos import public_assets

    src = tmp_path / "wide.jpg"
    Image.new("RGB", (3000, 900), (30, 90, 160)).save(src)      # 10:3
    object.__setattr__(scanned.ui, "landing_image", src)

    written = public_assets.build(scanned, force=True)
    assert written
    for name in written:
        with Image.open(public_assets.public_dir(scanned) / name) as im:
            w, h = im.size
            assert abs(w / h - 3000 / 900) < 0.02, f"{name} is {w}x{h}"


def test_icons_crop_rather_than_squash(scanned, tmp_path):
    """Square is unavoidable for an icon, but a squashed face is not.

    The source is half red and half blue down the middle; a centre-crop keeps
    both halves in the same proportion, a squash would too -- so this checks
    the geometry instead: a circle must stay circular.
    """
    from PIL import Image, ImageDraw

    from harelphotos import public_assets

    src = tmp_path / "circle.jpg"
    im = Image.new("RGB", (1000, 500), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([375, 75, 625, 325], fill=(0, 0, 0))              # a true circle
    im.save(src)
    object.__setattr__(scanned.ui, "app_icon", src)
    public_assets.build_icons(scanned, force=True)

    with Image.open(public_assets.public_dir(scanned) / "icon-192.png") as out:
        grey = out.convert("L")
        # The bounding box of the dark pixels, not a chord through the middle:
        # the circle is not centred in the source, so a row through the centre
        # of the *output* would cut a short chord and look like distortion.
        dark = grey.point(lambda v: 255 if v < 128 else 0)
        box = dark.getbbox()
        assert box, "the circle vanished"
        wide, tall = box[2] - box[0], box[3] - box[1]
        # Squashing a 2:1 source into a square would make the circle half as
        # tall as it is wide; cropping keeps it round.
        assert abs(wide - tall) <= max(2, 0.04 * wide), (
            f"the circle came out {wide} wide and {tall} tall — squashed, not cropped"
        )


def test_public_assets_are_built_when_the_app_starts(scanned, tmp_path):
    """Under gunicorn nothing else calls the builder.

    `serve` and `init` used to be the only callers, so in production -- where
    gunicorn imports harelphotos.wsgi:app directly -- a configured
    landing_image was never turned into anything and simply never appeared.
    """
    from PIL import Image

    from harelphotos import public_assets
    from harelphotos.web import create_app

    src = tmp_path / "hero.jpg"
    Image.new("RGB", (1400, 700), (200, 40, 40)).save(src)
    object.__setattr__(scanned.ui, "landing_image", src)
    for p in public_assets.public_dir(scanned).glob("*"):
        p.unlink()

    create_app(scanned, require_login=True)

    assert (public_assets.public_dir(scanned) / "landing-640.jpeg").is_file()
    assert public_assets.icons(scanned), "no icons were produced at startup"


def test_hero_width_controls_both_the_files_and_the_drawn_size(scanned, tmp_path):
    """640 was a hardcoded choice of mine; it is the site owner's to make."""
    from PIL import Image

    from harelphotos import public_assets

    src = tmp_path / "hero2.jpg"
    Image.new("RGB", (2000, 1000), (40, 80, 160)).save(src)
    object.__setattr__(scanned.ui, "landing_image", src)
    object.__setattr__(scanned.ui, "hero_width", 420)

    public_assets.build(scanned, force=True)
    # The display width and a 2x companion for high-density screens.
    assert public_assets.widths(scanned) == (420, 840)
    for w in (420, 840):
        with Image.open(public_assets.public_dir(scanned) / f"landing-{w}.avif") as im:
            assert im.size[0] == w
    assert public_assets.hero(scanned)["width"] == 420


def test_a_configured_hero_width_reaches_the_page(scanned, tmp_path):
    from PIL import Image

    from harelphotos import public_assets
    from harelphotos.web import create_app

    src = tmp_path / "hero3.jpg"
    Image.new("RGB", (2000, 1000), (40, 80, 160)).save(src)
    object.__setattr__(scanned.ui, "landing_image", src)
    object.__setattr__(scanned.ui, "hero_width", 380)
    for p in public_assets.public_dir(scanned).glob("*"):
        p.unlink()

    app = create_app(scanned, require_login=True)
    app.config.update(TESTING=True)
    body = app.test_client().get("/login").get_data(as_text=True)
    assert "max-inline-size: 380px" in body
    assert "/public/landing-380." in body


# ------------------------------------------------------- very large albums

def _album_of(cfg, n, tmp_path):
    """Index n photos in one directory, without deriving images for them."""
    from harelphotos import db as db_mod, scanner

    d = cfg.photo_root / "huge"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        fixtures.make_jpeg(d / f"p{i:04d}.jpg", size=(80, 60))
    conn = db_mod.open_index(cfg.index_db)
    scanner.scan(cfg, conn)
    conn.close()


def test_a_small_album_has_no_pager(client):
    body = client.get("/a/2019/01/").get_data(as_text=True)
    assert "pager" not in body


def test_a_large_album_is_split_into_pages(scanned, tmp_path):
    """A directory of thousands of photos is megabytes of HTML and tens of
    thousands of DOM nodes, which a phone feels."""
    from harelphotos.web import create_app

    _album_of(scanned, 25, tmp_path)
    object.__setattr__(scanned.ui, "album_page_size", 10)
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()

    first = c.get("/a/huge/").get_data(as_text=True)
    assert first.count('class="tile"') == 10
    assert "photos 1–10 of 25" in first
    assert 'href="?page=2"' in first

    last = c.get("/a/huge/?page=3").get_data(as_text=True)
    assert last.count('class="tile"') == 5
    assert "photos 21–25 of 25" in last


def test_page_numbers_out_of_range_are_clamped_not_errors(scanned, tmp_path):
    from harelphotos.web import create_app

    _album_of(scanned, 25, tmp_path)
    object.__setattr__(scanned.ui, "album_page_size", 10)
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()
    for bad in ("0", "-3", "99", "banana", ""):
        r = c.get(f"/a/huge/?page={bad}")
        assert r.status_code == 200, bad


def test_a_photo_links_back_to_its_own_page_of_the_album(scanned, tmp_path):
    """Otherwise leaving a photo from page 3 dumps you at the start of a
    several-thousand-photo album."""
    import json as _json

    from harelphotos.web import create_app

    _album_of(scanned, 25, tmp_path)
    object.__setattr__(scanned.ui, "album_page_size", 10)
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()

    body = c.get("/p/huge/p0022.jpg").get_data(as_text=True)
    nav = _json.loads(body.split('id="nav-data" type="application/json">')[1]
                      .split("</script>")[0])
    assert nav["album"].endswith("?page=3"), nav["album"]

    # And a photo on the first page carries no page at all.
    body = c.get("/p/huge/p0001.jpg").get_data(as_text=True)
    nav = _json.loads(body.split('id="nav-data" type="application/json">')[1]
                      .split("</script>")[0])
    assert "?page=" not in nav["album"], nav["album"]


def test_subdirectory_cards_can_omit_the_date_range(scanned):
    """On a tree already organised by date the card repeats the folder name,
    and less accurately: one photo with a wrong clock widens the range."""
    from harelphotos.web import create_app

    def body(show):
        object.__setattr__(scanned.ui, "dir_card_dates", show)
        app = create_app(scanned, require_login=False)
        app.config.update(TESTING=True)
        return app.test_client().get("/a/2019/").get_data(as_text=True)

    with_dates = body(True)
    without = body(False)

    card_with = with_dates.split('class="card-sub"')[1][:200]
    card_without = without.split('class="card-sub"')[1][:200]
    assert "·" in card_with, "the fixture has no date range to hide"
    assert "·" not in card_without
    # The photo count stays either way.
    assert "photo" in card_with and "photo" in card_without


def test_the_albums_own_header_keeps_its_dates(scanned):
    """Turning the cards off must not take the header with it."""
    from harelphotos.web import create_app

    object.__setattr__(scanned.ui, "dir_card_dates", False)
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    head = app.test_client().get("/a/2019/01/").get_data(as_text=True)
    head = head.split('class="meta"')[1][:200]
    assert "·" in head, head


# --------------------------------------------------- originals and X-Sendfile

def test_originals_are_not_handed_to_apache(scanned, tmp_path):
    """Apache sends a file the application names only if it is under an
    XSendFilePath, and that lists the derived tree alone -- the photo tree
    cannot be added, because it lives in a home directory Apache cannot read.

    Naming a file outside the list does not fail loudly: mod_xsendfile answers
    404. So "Download original", and the full-size image shown for a photo
    whose copies are not generated yet, were silently missing in production
    while working in development, where nothing hands off to Apache at all.
    """
    from harelphotos.web import create_app

    object.__setattr__(scanned, "sendfile_header", "X-Sendfile")
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()

    for url in ("/orig/2019/01/a.jpg", "/i/orig/2019/01/a.jpg"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "X-Sendfile" not in r.headers, url
        assert r.data, f"{url} served no bytes"


def test_derived_images_are_still_handed_to_apache(scanned):
    """The hand-off is the point of the setting; only originals are exempt."""
    import re

    from harelphotos.web import create_app

    object.__setattr__(scanned, "sendfile_header", "X-Sendfile")
    app = create_app(scanned, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()

    body = c.get("/a/2019/01/").get_data(as_text=True)
    url = re.search(r'src="(/i/\d+/[^"]+)"', body).group(1)
    r = c.get(url)
    assert r.status_code == 200
    assert "X-Sendfile" in r.headers
    assert str(scanned.derived_root) in r.headers["X-Sendfile"]


def test_the_source_link_is_offered_to_everyone_who_uses_the_site(scanned):
    """The AGPL's section 13: a modified version must offer its source to the
    people reaching it over a network. Shown on the login page and on every
    page a signed-in viewer sees, not only on the one they passed through."""
    from harelphotos.web import create_app

    object.__setattr__(scanned.ui, "source_url", "https://example.invalid/src")
    # The login gate on, so the landing page renders rather than redirecting.
    app = create_app(scanned, require_login=True)
    app.config.update(TESTING=True)
    c = app.test_client()

    assert "https://example.invalid/src" in c.get("/login").get_data(as_text=True)


def test_the_source_link_can_point_at_a_fork(scanned):
    """Which is the whole point: someone running changed code has to offer
    *their* source, not the upstream project's."""
    from harelphotos.web import create_app

    object.__setattr__(scanned.ui, "source_url", "https://example.invalid/my-fork")
    app = create_app(scanned, require_login=True)
    app.config.update(TESTING=True)
    body = app.test_client().get("/login").get_data(as_text=True)
    assert "my-fork" in body
    assert "github.com/nyh/harelphotos" not in body
