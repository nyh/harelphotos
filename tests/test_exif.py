"""EXIF parsing (DESIGN.md 9.3)."""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import io
import time

from PIL import Image

from harelphotos import exif

from . import fixtures


def header_of(path):
    return exif.read_header(path.read_bytes(), path)


def test_dimensions_without_exif(tmp_path):
    p = fixtures.make_jpeg(tmp_path / "a.jpg", size=(100, 50))
    h = header_of(p)
    assert (h.width, h.height) == (100, 50)
    assert h.taken is None
    assert h.error is None


def test_datetime_original(tmp_path):
    p = fixtures.make_jpeg(tmp_path / "a.jpg", taken="2019:08:14 16:50:00")
    h = header_of(p)
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(h.taken)) == "2019-08-14 16:50"


def test_datetime_is_local_wall_time_not_converted(tmp_path, monkeypatch):
    """The shifted-holiday-snaps bug (DESIGN.md 11.2).

    The tag has no timezone; it is what the camera's clock said. Whatever TZ
    the server runs in, the rendered wall-clock time must match the tag.
    """
    p = fixtures.make_jpeg(tmp_path / "a.jpg", taken="2019:08:14 16:50:00")
    seen = []
    for tz in ("UTC", "Asia/Jerusalem", "America/Los_Angeles", "Pacific/Kiritimati"):
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        h = header_of(p)
        seen.append(time.strftime("%Y-%m-%d %H:%M", time.localtime(h.taken)))
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()
    assert seen == ["2019-08-14 16:50"] * 4


def test_orientation_swaps_reported_dimensions(tmp_path):
    # Values 5-8 mean the image is stored rotated; the grid needs the
    # dimensions as displayed, or half the aspect ratios are wrong.
    p = fixtures.make_jpeg(tmp_path / "a.jpg", size=(100, 50), orientation=6)
    h = header_of(p)
    assert (h.width, h.height) == (50, 100)
    p = fixtures.make_jpeg(tmp_path / "b.jpg", size=(100, 50), orientation=1)
    assert header_of(p).width == 100


def test_gps_extracted(tmp_path):
    p = fixtures.make_jpeg(tmp_path / "a.jpg", gps=(32.0, 34.0))
    h = header_of(p)
    assert abs(h.exif["lat"] - 32.0) < 0.02
    assert abs(h.exif["lon"] - 34.0) < 0.02


def test_implausible_dates_rejected():
    # Picasa stamps year 4500 on scans it cannot date; three such files turned
    # up in the first 250 real photos indexed, and each one would otherwise
    # sort last in its album forever and wreck the album's date range.
    assert exif.parse_datetime("4500:12:31 23:00:00") is None
    assert exif.parse_datetime("1000:01:01 00:00:00") is None
    # The lower bound is deliberately generous: photography starts in 1826,
    # and a scan of an old family photograph may carry a hand-set date.
    assert exif.parse_datetime("1899:01:01 00:00:00") is not None
    assert exif.parse_datetime("1935:06:01 12:00:00") is not None


def test_garbage_never_raises():
    for bad in (None, "", "   ", b"\xff\xfe", "not a date", "0000:00:00 00:00:00", 12345):
        assert exif.parse_datetime(bad) is None


def test_corrupt_file_reports_an_error():
    h = exif.read_header(b"\xff\xd8 definitely not a jpeg")
    assert h.error
    assert h.width is None


def test_truncated_header_falls_back_to_the_file(tmp_path):
    # If the head buffer was not enough, reopening the file must still work.
    p = fixtures.make_jpeg(tmp_path / "a.jpg", size=(120, 90))
    h = exif.read_header(b"", p)
    assert (h.width, h.height) == (120, 90)


def test_camera_name_does_not_repeat_the_make(tmp_path):
    im = Image.new("RGB", (10, 10))
    ex = im.getexif()
    ex[271] = "NIKON"          # Make
    ex[272] = "NIKON D90"      # Model
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex)
    h = exif.read_header(buf.getvalue())
    assert h.exif["camera"] == "NIKON D90"
