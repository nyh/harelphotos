"""EXIF extraction (DESIGN.md 9.3).

Everything here is best-effort. Malformed EXIF is extremely common in a
collection spanning decades, and a photo with unreadable metadata is still a
photo we must index and display — so nothing in this module raises.

Note this runs in scan **phase 2**, from bytes already read for the content
signature, with no pixel decoding: ``Image.open`` parses only the header.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

# Pillow needs the SOF marker for dimensions and the APP1 segment for EXIF,
# both near the start. 128 KB covers both comfortably, including the embedded
# preview thumbnails that push SOF further in.
HEAD_BYTES = 128 * 1024
TAIL_BYTES = 64 * 1024

# EXIF orientation values 5-8 swap width and height.
_TRANSPOSED = {5, 6, 7, 8}


def _rational(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return f


def _clean(value: Any) -> str | None:
    """EXIF strings are frequently padded, NUL-terminated or empty."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "replace")
        except Exception:
            return None
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\x00", "").strip()
    return value or None


def parse_datetime(raw: Any) -> int | None:
    """EXIF DateTimeOriginal -> unix epoch, interpreted as LOCAL wall time.

    The tag carries no timezone: it is what the camera's clock said. We convert
    with ``timestamp()`` on a naive datetime, which uses the local zone, and
    the display side renders it back the same way. What must never happen is a
    round-trip through UTC that shifts everyone's holiday photos (DESIGN.md
    11.2).
    """
    s = _clean(raw)
    if not s:
        return None
    # Canonical form is "YYYY:MM:DD HH:MM:SS"; tolerate the common variants.
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[: len(fmt) + 6], fmt)
        except ValueError:
            continue
        if not _plausible(dt):
            return None
        try:
            return int(dt.timestamp())
        except (OverflowError, OSError, ValueError):
            return None
    return None


# Photography starts in 1826, and a scan of a Victorian portrait may carry a
# deliberately-set date, so the lower bound is generous.
EARLIEST_YEAR = 1826


def _plausible(dt: datetime) -> bool:
    """Reject the placeholder dates that real collections are full of.

    Two failure modes seen in the wild, both of which would otherwise sort a
    photo to one end of every album forever and wreck the album's date range:

    * cameras with a dead clock battery stamping 1970 or 1980;
    * Picasa writing year 4500 on scans whose date it could not determine —
      three of those turned up in the first 250 real photos indexed.
    """
    if dt.year < EARLIEST_YEAR:
        return False
    # Tomorrow, to allow for a camera clock set to another timezone.
    return dt.year <= time.localtime().tm_year + 1


def _gps_degrees(value: Any) -> float | None:
    """GPS coordinates are ((d,1),(m,1),(s,100)) rationals."""
    try:
        d, m, s = value
        return _rational(d) + _rational(m) / 60.0 + _rational(s) / 3600.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _extract_gps(exif) -> tuple[float | None, float | None]:
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:
        return None, None
    if not gps:
        return None, None
    try:
        lat = _gps_degrees(gps.get(ExifTags.GPS.GPSLatitude))
        lon = _gps_degrees(gps.get(ExifTags.GPS.GPSLongitude))
    except Exception:
        return None, None
    if lat is None or lon is None:
        return None, None
    if _clean(gps.get(ExifTags.GPS.GPSLatitudeRef)) == "S":
        lat = -lat
    if _clean(gps.get(ExifTags.GPS.GPSLongitudeRef)) == "W":
        lon = -lon
    # 0,0 is in the Gulf of Guinea and is overwhelmingly a bug, not a holiday.
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None, None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None
    return lat, lon


class PhotoHeader:
    """What phase 2 learns about a photo without decoding any pixels."""

    __slots__ = ("width", "height", "orientation", "taken", "exif", "error")

    def __init__(self) -> None:
        self.width: int | None = None
        self.height: int | None = None
        self.orientation: int = 1
        self.taken: int | None = None
        self.exif: dict[str, Any] = {}
        self.error: str | None = None

    @property
    def exif_json(self) -> str | None:
        return json.dumps(self.exif, separators=(",", ":"), sort_keys=True) if self.exif else None


def read_header(data: bytes, path: Path | None = None) -> PhotoHeader:
    """Parse dimensions and EXIF from a photo's leading bytes.

    ``path`` is a fallback: if the head buffer was not enough (an unusually
    large embedded preview can push the SOF marker past it), we reopen the
    file. That costs an extra round trip on a slow mount, so it is the
    exception rather than the rule.
    """
    out = PhotoHeader()
    im = None
    try:
        im = Image.open(io.BytesIO(data))
        im.load_prepare = None  # never triggers a decode; we only read metadata
    except Exception:
        if path is not None:
            try:
                im = Image.open(path)
            except Exception as e:
                out.error = f"unreadable image header: {e}"
                return out
        else:
            out.error = "unreadable image header"
            return out

    try:
        out.width, out.height = im.size
    except Exception:
        pass

    try:
        exif = im.getexif()
    except Exception:
        exif = None

    if exif:
        try:
            out.orientation = int(exif.get(274, 1) or 1)   # 274 = Orientation
        except (TypeError, ValueError):
            out.orientation = 1

        try:
            ifd = exif.get_ifd(ExifTags.IFD.Exif)
        except Exception:
            ifd = {}

        out.taken = parse_datetime(
            ifd.get(ExifTags.Base.DateTimeOriginal.value)
            or ifd.get(ExifTags.Base.DateTimeDigitized.value)
            or exif.get(ExifTags.Base.DateTime.value)
        )

        meta: dict[str, Any] = {}
        make = _clean(exif.get(ExifTags.Base.Make.value))
        model = _clean(exif.get(ExifTags.Base.Model.value))
        if make and model and model.lower().startswith(make.lower()):
            make = None      # "NIKON" + "NIKON D90" reads badly; keep the model
        camera = " ".join(x for x in (make, model) if x)
        if camera:
            meta["camera"] = camera
        for key, tag in (
            ("lens", ExifTags.Base.LensModel),
            ("software", ExifTags.Base.Software),
        ):
            v = _clean(ifd.get(tag.value) or exif.get(tag.value))
            if v:
                meta[key] = v
        exposure = _rational(ifd.get(ExifTags.Base.ExposureTime.value))
        if exposure:
            meta["exposure"] = exposure
        fnum = _rational(ifd.get(ExifTags.Base.FNumber.value))
        if fnum:
            meta["fnumber"] = round(fnum, 2)
        iso = ifd.get(ExifTags.Base.ISOSpeedRatings.value)
        if isinstance(iso, (list, tuple)):
            iso = iso[0] if iso else None
        try:
            if iso is not None:
                meta["iso"] = int(iso)
        except (TypeError, ValueError):
            pass
        focal = _rational(ifd.get(ExifTags.Base.FocalLength.value))
        if focal:
            meta["focal"] = round(focal, 1)
        focal35 = _rational(ifd.get(ExifTags.Base.FocalLengthIn35mmFilm.value))
        if focal35:
            meta["focal35"] = round(focal35)

        lat, lon = _extract_gps(exif)
        if lat is not None:
            meta["lat"] = round(lat, 7)
            meta["lon"] = round(lon, 7)
        out.exif = meta

    # Record the *displayed* dimensions, so the grid's aspect ratios and the
    # srcset widths are right without every consumer re-checking orientation.
    if out.orientation in _TRANSPOSED and out.width and out.height:
        out.width, out.height = out.height, out.width

    try:
        im.close()
    except Exception:
        pass
    return out
