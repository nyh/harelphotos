"""The contrib script that dates WhatsApp files from their names.

Worth testing despite living in contrib/: it is the only thing in the project
that writes to the photo tree, so the cases it must *not* touch matter as much
as the ones it must.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import importlib.util
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "fixup_whatsapp_dates",
    Path(__file__).resolve().parent.parent / "contrib" / "fixup_whatsapp_dates.py",
)
fixup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixup)


def at(y, m, d, secs):
    return datetime(y, m, d).timestamp() + secs


@pytest.mark.parametrize("name, expected", [
    # The ordinary case, and the counter carried through as seconds past noon
    # so a day's photographs keep the order WhatsApp saved them in.
    ("IMG-20260716-WA0011.jpg", at(2026, 7, 16, 12 * 3600 + 11)),
    ("VID-20260716-WA0012.mp4", at(2026, 7, 16, 12 * 3600 + 12)),
    # A second download of the same item. Anchoring the pattern at the end
    # would miss these -- there are two in one real album.
    ("IMG-20260731-WA0122(1).jpg", at(2026, 7, 31, 12 * 3600 + 122)),
    ("IMG-20260723-WA0066 (copy).jpg", at(2026, 7, 23, 12 * 3600 + 66)),
    ("img-20260716-wa0011.jpg", at(2026, 7, 16, 12 * 3600 + 11)),
])
def test_the_date_comes_out_of_the_name(name, expected):
    assert fixup.timestamp_for(name) == expected


@pytest.mark.parametrize("name", [
    "PXL_20260715_130424534.jpg",       # Pixel: has real EXIF, leave it alone
    "IMG_20260716_002422.jpg",          # Android camera: likewise
    "IMG-2026071-WA0011.jpg",           # too few digits
    "IMG-20261332-WA0011.jpg",          # no such date
    "holiday.jpg",
    "WA0011.jpg",
    "IMG-20260716-XX0011.jpg",
])
def test_anything_else_is_left_alone(name):
    assert fixup.timestamp_for(name) is None


def test_noon_is_far_enough_from_midnight_to_survive_a_timezone():
    """The reason for midday rather than the start or end of the day: a
    timestamp read up to eleven hours away still lands on the right date.

    The counter is capped so that stays true at the top of its range. Without
    the cap a four-digit counter reaches 14:46, and eleven hours past that is
    the following morning.
    """
    for name in ("IMG-20260716-WA0001.jpg", "IMG-20260716-WA0264.jpg",
                 "IMG-20260716-WA9999.jpg"):
        ts = fixup.timestamp_for(name)
        assert datetime.fromtimestamp(ts).hour == 12, name
        assert datetime.fromtimestamp(ts - 11 * 3600).day == 16, name
        assert datetime.fromtimestamp(ts + 11 * 3600).day == 16, name


def test_it_writes_nothing_without_apply(tmp_path, capsys):
    f = tmp_path / "IMG-20260716-WA0011.jpg"
    f.write_bytes(b"x")
    os.utime(f, (1_000_000, 1_000_000))

    assert fixup.main([str(tmp_path)]) == 0
    assert f.stat().st_mtime == 1_000_000            # untouched
    out = capsys.readouterr().out
    assert "would change 1" in out
    assert "Pass --apply" in out

    assert fixup.main([str(tmp_path), "--apply"]) == 0
    assert f.stat().st_mtime == fixup.timestamp_for(f.name)
    assert "changed 1" in capsys.readouterr().out


def test_it_walks_directories_and_leaves_other_photographs_untouched(tmp_path):
    (tmp_path / "sub").mkdir()
    wa = tmp_path / "sub" / "IMG-20260716-WA0011.jpg"
    other = tmp_path / "PXL_20260715_130424534.jpg"
    for f in (wa, other):
        f.write_bytes(b"x")
        os.utime(f, (1_000_000, 1_000_000))

    fixup.main([str(tmp_path), "--apply"])

    assert wa.stat().st_mtime == fixup.timestamp_for(wa.name)
    assert other.stat().st_mtime == 1_000_000        # not a WhatsApp name


def test_running_it_twice_changes_nothing_the_second_time(tmp_path, capsys):
    f = tmp_path / "IMG-20260716-WA0011.jpg"
    f.write_bytes(b"x")
    fixup.main([str(tmp_path), "--apply"])
    capsys.readouterr()
    fixup.main([str(tmp_path), "--apply"])
    out = capsys.readouterr().out
    assert "changed 0" in out and "already correct 1" in out


def test_a_scan_picks_the_new_date_up_and_re_encodes_nothing(tmp_path):
    """The whole point of doing it this way rather than in the scanner: touch
    the file, and an ordinary scan carries the date into the index while
    leaving every generated image alone."""
    from harelphotos import scanner
    from harelphotos.queries import Index, Viewer

    from . import fixtures

    photos = tmp_path / "pictures" / "a"
    fixtures.make_jpeg(photos / "IMG-20260716-WA0011.jpg", size=(900, 600))
    fixtures.make_jpeg(photos / "PXL_20260715_130424534.jpg", size=(900, 600))
    # Both copied on the same day, as a folder moved off a phone would be.
    copied = time.mktime((2026, 8, 29, 2, 39, 0, 0, 0, -1))
    for f in photos.iterdir():
        os.utime(f, (copied, copied))

    cfg = fixtures.make_config(tmp_path, tmp_path / "pictures")
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    keys = lambda: sorted(r[0] for r in conn.execute("SELECT deriv_key FROM photos"))
    before = keys()
    assert all(before)

    fixup.main([str(photos), "--apply"])
    stats = scanner.scan(cfg, conn)

    assert stats.photos_derived == 0, "a date fix must not cost a re-encode"
    assert keys() == before
    conn.close()

    conn = __import__("harelphotos.db", fromlist=["db"]).open_index(
        cfg.index_db, read_only=True)
    ix = Index(conn, cfg)
    admin = Viewer(token=None, name="", is_admin=True)
    dates = {p.name: datetime.fromtimestamp(p.when) for p in ix.photos(
        ix.album("a", admin), admin)}
    conn.close()

    assert dates["IMG-20260716-WA0011.jpg"].date() == datetime(2026, 7, 16).date()
    # The camera photograph is untouched: it has EXIF and never needed this.
    assert dates["PXL_20260715_130424534.jpg"].date() == datetime(2026, 8, 29).date()
