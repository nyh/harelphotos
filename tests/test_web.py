"""The web application (DESIGN.md 10, 11)."""

from __future__ import annotations

import pytest

from harelphotos import scanner
from harelphotos.web import create_app

from . import fixtures


@pytest.fixture
def client(tmp_path):
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    app = create_app(cfg)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.harelphotos_cfg = cfg
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
    app = create_app(cfg)
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
    # 800x600 sources: the 1280 and 2048 tiers do not exist and must not be
    # advertised, or the browser fetches a 404.
    assert "/i/1280/" not in body
    assert "/i/2048/" not in body


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
    assert r.get_json()["photos"] == 8


def test_static_assets_are_served(client):
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_grid_carries_aspect_ratios_for_the_layout(client):
    body = client.get("/a/2019/01/").get_data(as_text=True)
    assert 'data-ar="1.3333"' in body        # 800x600
