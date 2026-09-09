"""Build small synthetic photo trees for the scanner tests.

Deliberately includes the awkward cases a real twenty-year collection has:
photos with and without EXIF dates, odd orientations, a corrupt file, an empty
directory, unicode names.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from harelphotos import config as config_mod
from harelphotos import db


def make_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = (64, 48),
    taken: str | None = None,
    orientation: int | None = None,
    colour: tuple[int, int, int] = (120, 90, 60),
    gps: tuple[float, float] | None = None,
) -> Path:
    """Write a tiny JPEG, optionally with EXIF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, colour)
    exif = im.getexif()
    if taken:
        exif[36867] = taken            # DateTimeOriginal lives in the Exif IFD
        ifd = exif.get_ifd(0x8769)
        ifd[36867] = taken
        exif[306] = taken              # DateTime, as a fallback path
    if orientation:
        exif[274] = orientation
    if gps:
        lat, lon = gps
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd[1] = "N" if lat >= 0 else "S"
        gps_ifd[2] = (abs(int(lat)), int(abs(lat) % 1 * 60), 0.0)
        gps_ifd[3] = "E" if lon >= 0 else "W"
        gps_ifd[4] = (abs(int(lon)), int(abs(lon) % 1 * 60), 0.0)
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    path.write_bytes(buf.getvalue())
    return path


def make_tree(root: Path) -> Path:
    """A small tree exercising most of the scanner's decisions."""
    root.mkdir(parents=True, exist_ok=True)

    # A year with month subdirectories, the shape the real collection uses.
    make_jpeg(root / "2019" / "01" / "a.jpg", taken="2019:01:15 10:00:00")
    make_jpeg(root / "2019" / "01" / "b.jpg", taken="2019:01:16 11:00:00")
    make_jpeg(root / "2019" / "02" / "c.jpg", taken="2019:02:20 09:30:00")
    # No EXIF date at all: must fall back to mtime for sorting.
    make_jpeg(root / "2019" / "02" / "d.jpg")
    (root / "2019" / ".album.toml").write_text(
        'title = "Twenty Nineteen"\ndirsort = "-name"\n', encoding="utf-8"
    )

    # A restricted subtree.
    make_jpeg(root / "private" / "secret.jpg", taken="2020:05:05 05:05:05")
    (root / "private" / ".album.toml").write_text('allow = ["nyh"]\n', encoding="utf-8")

    # A directory that sorts by an explicit key rather than its name.
    make_jpeg(root / "ancient" / "old.jpg")
    (root / "ancient" / ".album.toml").write_text(
        'title = "Scans"\nsort_key = "1975"\nlocation = "Haifa, Israel"\n'
        'cover = "old.jpg"\n',
        encoding="utf-8",
    )

    # Things that must be ignored.
    (root / "2019" / "01" / "notes.txt").write_text("not a photo", encoding="utf-8")
    (root / "2019" / "01" / "Thumbs.db").write_bytes(b"junk")
    (root / ".hidden").mkdir(exist_ok=True)
    make_jpeg(root / ".hidden" / "nope.jpg")

    # A directory holding only non-photos: appears, but with no photos beneath.
    (root / "movies").mkdir(exist_ok=True)
    (root / "movies" / "clip.mp4").write_bytes(b"not a photo either")

    # Uppercase extension, and a unicode name.
    make_jpeg(root / "2019" / "02" / "UPPER.JPG", taken="2019:02:21 12:00:00")
    make_jpeg(root / "2019" / "02" / "שלום.jpg", taken="2019:02:22 12:00:00")

    return root


def make_config(tmp_path: Path, photo_root: Path) -> config_mod.Config:
    """A Config pointing at a fixture tree, with state under tmp_path."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return config_mod.from_dict(
        {
            "photo_root": str(photo_root),
            "derived_root": str(state / "derived"),
            "index_db": str(state / "index.sqlite"),
            "users_file": str(tmp_path / "users.toml"),
            "secret_key_file": str(tmp_path / "secret_key"),
        },
        Path("test-config.toml"),
    )


def fresh_index(cfg: config_mod.Config):
    return db.create_index(cfg.index_db)
