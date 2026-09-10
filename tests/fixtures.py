"""Build small synthetic photo trees for the scanner tests.

Deliberately includes the awkward cases a real twenty-year collection has:
photos with and without EXIF dates, odd orientations, a corrupt file, an empty
directory, unicode names.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from harelphotos import config as config_mod
from harelphotos import db


# Small on purpose. At 800x600 three tiers were encoded per photo and the suite
# scans once per test; at 400x300 it is two, and smaller ones -- which took the
# whole suite from 8m34s to 3m05s. Tests that care about the size of a photo
# pass their own.
DEFAULT_SIZE = (400, 300)


def make_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = DEFAULT_SIZE,
    taken: str | None = None,
    orientation: int | None = None,
    color: tuple[int, int, int] = (120, 90, 60),
    gps: tuple[float, float] | None = None,
) -> Path:
    """Write a tiny JPEG, optionally with EXIF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
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
    # Big enough to have every tier: several tests check the srcset ladder,
    # and a 400px photo legitimately has no 1280 tier. The rest of the tree
    # stays small, which is what keeps the suite quick.
    make_jpeg(root / "2019" / "01" / "a.jpg", taken="2019:01:15 10:00:00",
              size=(800, 600))
    make_jpeg(root / "2019" / "01" / "b.jpg", taken="2019:01:16 11:00:00",
              size=(800, 600))
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
    # A space in the name. Not exotic: 107 of the real collection's photos
    # have one, and it broke `srcset` outright, because whitespace is what
    # separates a candidate URL from its width descriptor there.
    make_jpeg(root / "2019" / "02" / "zPic 4.jpg", taken="2019:02:23 12:00:00")

    return root


def make_config(tmp_path: Path, photo_root: Path) -> config_mod.Config:
    """A Config pointing at a fixture tree, with state under tmp_path."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    # The app needs a signing key to start.
    key = tmp_path / "secret_key"
    if not key.exists():
        key.write_bytes(b"0123456789abcdef0123456789abcdef")
    return config_mod.from_dict(
        {
            "photo_root": str(photo_root),
            "derived_root": str(state / "derived"),
            "index_db": str(state / "index.sqlite"),
            "users_file": str(tmp_path / "users.toml"),
            "secret_key_file": str(tmp_path / "secret_key"),
            # One process: the fixture trees hold a handful of tiny photos, so
            # forking a worker per core cost more than the work.
            "scan": {"jobs": 1},
            # The fastest AVIF setting. Encoding was 1.6s of every 2.7s scan
            # and the suite scans once per test; nothing here asserts anything
            # about compression, only about which files exist and how large
            # their pixels are.
            "encode": {"speed": 10},
        },
        Path("test-config.toml"),
    )


def fresh_index(cfg: config_mod.Config):
    return db.create_index(cfg.index_db)


def add_user(cfg: config_mod.Config, token: str = "nyh", password: str = "nyh",
             admin: bool = False, name: str | None = None):
    """Add an account to the fixture's users.toml."""
    from harelphotos import users as users_mod

    existing = users_mod.load(cfg.users_file)
    table = dict(existing.by_token)
    table[token] = users_mod.User(
        token=token,
        name=name or token,
        password_hash=users_mod.hash_password(password),
        admin=admin,
    )
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))
    return table[token]
