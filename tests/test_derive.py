"""The derivative pipeline (DESIGN.md 9.1)."""

from __future__ import annotations

import json

from PIL import Image

from harelphotos import derive, scanner

from . import fixtures


def test_derives_every_configured_tier(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(3000, 2000))
    cfg = fixtures.make_config(tmp_path, photos)
    res = derive.derive(photos / "a.jpg", "a.jpg", cfg)
    assert res.error is None
    assert res.tiers == [2048, 1280, 512, 256]
    for tier in res.tiers:
        p = derive.derived_path(cfg, tier, "a.jpg")
        assert p.exists(), p
        with Image.open(p) as im:
            assert max(im.size) == tier
            assert im.format == "AVIF"


def test_aspect_ratio_is_preserved_never_cropped(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "wide.jpg", size=(3000, 1000))     # 3:1
    cfg = fixtures.make_config(tmp_path, photos)
    derive.derive(photos / "wide.jpg", "wide.jpg", cfg)
    with Image.open(derive.derived_path(cfg, 512, "wide.jpg")) as im:
        assert im.size == (512, 171)       # 3:1 kept, not squared off


def test_never_upscales(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "small.jpg", size=(400, 300))
    cfg = fixtures.make_config(tmp_path, photos)
    res = derive.derive(photos / "small.jpg", "small.jpg", cfg)
    assert 2048 not in res.tiers and 1280 not in res.tiers
    for tier in res.tiers:
        with Image.open(derive.derived_path(cfg, tier, "small.jpg")) as im:
            assert max(im.size) <= 400, tier


def test_resolution_between_two_tiers_is_not_thrown_away(tmp_path):
    """A 1174 px photo must not end up with 512 px as its largest view.

    Skipping every tier above the original loses most of the resolution of
    anything falling between two tiers — which, in a collection spanning
    decades of cameras, is a great many photos.
    """
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "mid.jpg", size=(1174, 789))
    cfg = fixtures.make_config(tmp_path, photos)
    res = derive.derive(photos / "mid.jpg", "mid.jpg", cfg)
    assert 2048 not in res.tiers          # far bigger than the source: skipped
    assert 1280 in res.tiers              # the smallest covering tier...
    with Image.open(derive.derived_path(cfg, 1280, "mid.jpg")) as im:
        assert im.size == (1174, 789)     # ...holds the original's own size
    assert 512 in res.tiers and 256 in res.tiers


def test_a_derivative_bigger_than_the_original_is_discarded(tmp_path):
    # Re-encoding a very small photo can produce something larger. Serving
    # that would be worse than serving the original, so it is thrown away and
    # the web layer falls back to the original.
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "tiny.jpg", size=(40, 30))
    cfg = fixtures.make_config(tmp_path, photos)
    res = derive.derive(photos / "tiny.jpg", "tiny.jpg", cfg)
    assert res.error is None
    assert res.colour is not None
    original = (photos / "tiny.jpg").stat().st_size
    for tier in res.tiers:
        assert derive.derived_path(cfg, tier, "tiny.jpg").stat().st_size < original


def test_orientation_is_applied(tmp_path):
    photos = tmp_path / "pictures"
    # Stored 1000x600 but tagged as rotated: the output must be portrait.
    fixtures.make_jpeg(photos / "rot.jpg", size=(1000, 600), orientation=6)
    cfg = fixtures.make_config(tmp_path, photos)
    derive.derive(photos / "rot.jpg", "rot.jpg", cfg)
    with Image.open(derive.derived_path(cfg, 512, "rot.jpg")) as im:
        assert im.size[1] > im.size[0]


def test_derivatives_carry_no_metadata(tmp_path):
    """Smaller files, and no GPS leaking to whoever saves a thumbnail."""
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(
        photos / "gps.jpg", size=(1000, 800), taken="2019:08:14 16:50:00", gps=(32.1, 34.8)
    )
    cfg = fixtures.make_config(tmp_path, photos)
    derive.derive(photos / "gps.jpg", "gps.jpg", cfg)
    with Image.open(derive.derived_path(cfg, 512, "gps.jpg")) as im:
        assert not im.getexif()


def test_corrupt_file_reports_an_error(tmp_path):
    photos = tmp_path / "pictures"
    photos.mkdir()
    (photos / "bad.jpg").write_bytes(b"\xff\xd8 nope")
    cfg = fixtures.make_config(tmp_path, photos)
    res = derive.derive(photos / "bad.jpg", "bad.jpg", cfg)
    assert res.error and res.tiers == []


def test_no_temporary_files_are_left_behind(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    derive.derive(photos / "a.jpg", "a.jpg", cfg)
    leftovers = [p for p in cfg.derived_root.rglob("*") if ".tmp." in p.name]
    assert leftovers == []


def test_deriv_key_changes_with_every_setting_that_matters(tmp_path):
    photos = tmp_path / "pictures"
    photos.mkdir()
    cfg = fixtures.make_config(tmp_path, photos)
    base = derive.deriv_key(b"sig", cfg)

    assert derive.deriv_key(b"other", cfg) != base          # different photo
    for field, value in (
        ("recipe_version", 2),
        ("speed", 8),
        ("subsampling", "4:4:4"),
        ("format", "webp"),
        ("quality", {256: 99, 512: 48, 1280: 46, 2048: 45}),
    ):
        other = fixtures.make_config(tmp_path, photos)
        object.__setattr__(other.encode, field, value)
        assert derive.deriv_key(b"sig", other) != base, field

    # Changing the ladder must invalidate too, or a new tier is never built.
    from harelphotos.config import Sizes

    other = fixtures.make_config(tmp_path, photos)
    object.__setattr__(other, "sizes", Sizes(thumb=(256,), view=(1600,)))
    assert derive.deriv_key(b"sig", other) != base


def test_deriv_key_ignores_mtime(tmp_path):
    """The whole point: touching a file must not invalidate its derivatives."""
    photos = tmp_path / "pictures"
    photos.mkdir()
    cfg = fixtures.make_config(tmp_path, photos)
    assert derive.deriv_key(b"sig", cfg) == derive.deriv_key(b"sig", cfg)


# ------------------------------------------------------- through the scanner

def test_scan_generates_and_records_derivatives(tmp_path):
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    stats = scanner.scan(cfg, conn)
    assert stats.photos_derived == 8
    assert stats.derive_failed == 0
    assert stats.bytes_written > 0
    row = conn.execute("SELECT * FROM photos WHERE name = 'a.jpg'").fetchone()
    assert row["deriv_key"] is not None
    assert row["color"].startswith("#")
    # 800x600 fixtures: 512 and 256 downscaled, plus 1280 holding the
    # original's own size (2048 is far bigger, so skipped).
    assert json.loads(row["deriv_tiers"]) == [1280, 512, 256]
    conn.close()


def test_rescan_does_not_re_derive(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    assert scanner.scan(cfg, conn).photos_derived == 1
    assert scanner.scan(cfg, conn).photos_derived == 0
    conn.close()


def test_changing_the_recipe_re_derives(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    object.__setattr__(cfg.encode, "recipe_version", 2)
    assert scanner.scan(cfg, conn).photos_derived == 1
    conn.close()


def test_headers_only_skips_derivation(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    stats = scanner.scan(cfg, conn, headers_only=True)
    assert stats.photos_checked == 1
    assert stats.photos_derived == 0
    assert not cfg.derived_root.exists() or not any(cfg.derived_root.rglob("*.avif"))
    conn.close()


def test_deleting_a_photo_deletes_its_derivatives(tmp_path):
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    fixtures.make_jpeg(photos / "b.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    assert derive.derived_path(cfg, 512, "a.jpg").exists()

    (photos / "a.jpg").unlink()
    scanner.scan(cfg, conn)
    assert not derive.derived_path(cfg, 512, "a.jpg").exists()
    assert derive.derived_path(cfg, 512, "b.jpg").exists()
    conn.close()


def test_touching_a_file_does_not_re_derive(tmp_path):
    """End to end: the jhead -ft case must not cost a re-encode."""
    import os
    import time

    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    before = derive.derived_path(cfg, 512, "a.jpg").stat().st_mtime_ns

    future = time.time() + 10_000
    os.utime(photos / "a.jpg", (future, future))
    stats = scanner.scan(cfg, conn)

    assert stats.photos_unchanged == 1
    assert stats.photos_derived == 0
    assert derive.derived_path(cfg, 512, "a.jpg").stat().st_mtime_ns == before
    conn.close()


def test_scanning_one_subdirectory_leaves_other_derivatives_alone(tmp_path):
    """Regression: --dir must not delete derivatives outside its subtree.

    The orphan query was unscoped while the deletion was scoped, so scanning
    one directory silently deleted every other directory's derivative files
    while leaving the database claiming they were still there.
    """
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "keep" / "a.jpg", size=(1000, 800))
    fixtures.make_jpeg(photos / "other" / "b.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    assert derive.derived_path(cfg, 512, "keep/a.jpg").exists()

    stats = scanner.scan(cfg, conn, subpath="other")
    assert stats.files_removed == 0
    assert derive.derived_path(cfg, 512, "keep/a.jpg").exists()
    assert derive.derived_path(cfg, 512, "other/b.jpg").exists()
    conn.close()


def test_missing_derivative_files_are_detected_and_repaired(tmp_path):
    """The database and the derived tree can disagree; say so, and fix it.

    deriv_key still matches after files vanish, so a plain rescan would skip
    those photos forever.
    """
    photos = tmp_path / "pictures"
    fixtures.make_jpeg(photos / "a.jpg", size=(1000, 800))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)

    victim = derive.derived_path(cfg, 512, "a.jpg")
    victim.unlink()
    assert scanner.find_missing_derivatives(cfg, conn) == ["a.jpg"]

    # A plain rescan does NOT notice...
    assert scanner.scan(cfg, conn).photos_derived == 0
    assert not victim.exists()

    # ...but --repair does.
    stats = scanner.scan(cfg, conn, repair=True)
    assert stats.repaired == 1
    assert stats.photos_derived == 1
    assert victim.exists()
    assert scanner.find_missing_derivatives(cfg, conn) == []
    conn.close()
