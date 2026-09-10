"""``harelphotos check`` — report what is wrong, and what the index contains.

Read-only. Everything it reports is something the scan deliberately did not
raise on: a malformed .album.toml, an unreadable photo, a cover that points at
nothing. Those are logged and carried in the database precisely so that one bad
file cannot stop a two-hour scan — which only works if there is somewhere to go
and look at them afterwards.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .config import Config


@dataclass
class Report:
    dirs: int = 0
    photos: int = 0
    photos_with_headers: int = 0
    photos_with_dates: int = 0
    photos_with_gps: int = 0
    empty_dirs: list[str] = field(default_factory=list)
    config_errors: list[tuple[str, str]] = field(default_factory=list)
    photo_errors: list[tuple[str, str]] = field(default_factory=list)
    missing_covers: list[str] = field(default_factory=list)
    missing_derivatives: list[str] = field(default_factory=list)
    restricted: list[tuple[str, str]] = field(default_factory=list)
    date_range: tuple[int | None, int | None] = (None, None)
    biggest: list[tuple[str, int]] = field(default_factory=list)

    @property
    def problems(self) -> int:
        return (len(self.config_errors) + len(self.photo_errors)
                + len(self.missing_covers) + len(self.missing_derivatives))


def run(cfg: Config, conn: sqlite3.Connection, *, sample: int = 10,
        verify_files: bool = False) -> Report:
    r = Report()
    r.dirs = conn.execute("SELECT count(*) AS n FROM dirs").fetchone()["n"]
    r.photos = conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"]
    r.photos_with_headers = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE content_sig IS NOT NULL"
    ).fetchone()["n"]
    r.photos_with_dates = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE taken IS NOT NULL"
    ).fetchone()["n"]
    r.photos_with_gps = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE exif_json LIKE '%\"lat\"%'"
    ).fetchone()["n"]

    row = conn.execute(
        "SELECT min(taken) AS lo, max(taken) AS hi FROM photos WHERE taken IS NOT NULL"
    ).fetchone()
    r.date_range = (row["lo"], row["hi"])

    r.config_errors = [
        (d["path"] or ".", d["cfg_error"])
        for d in conn.execute(
            "SELECT path, cfg_error FROM dirs WHERE cfg_error IS NOT NULL ORDER BY path"
        )
    ]
    r.photo_errors = [
        (f"{p['path']}/{p['name']}" if p["path"] else p["name"], p["deriv_error"])
        for p in conn.execute(
            "SELECT d.path, p.name, p.deriv_error FROM photos p JOIN dirs d ON d.id = p.dir_id "
            "WHERE p.deriv_error IS NOT NULL ORDER BY d.path, p.name"
        )
    ]
    r.missing_covers = [
        d["path"] or "."
        for d in conn.execute(
            "SELECT path FROM dirs WHERE cover_spec IS NOT NULL "
            "AND cover_spec NOT LIKE 'auto%' AND cover_photo IS NULL ORDER BY path"
        )
    ]
    # Directories holding no photos anywhere beneath them: scripts, backups and
    # scratch directories that happen to live in the photo tree.
    r.empty_dirs = [
        d["path"] or "."
        for d in conn.execute(
            "SELECT path FROM dirs WHERE n_photos_rec = 0 AND path != '' ORDER BY path"
        )
    ]
    r.restricted = [
        (d["path"] or ".", d["acl_chain"])
        for d in conn.execute(
            "SELECT path, acl_chain FROM dirs WHERE acl_chain != '[]' ORDER BY path"
        )
    ]
    if verify_files:
        from .scanner import find_missing_derivatives

        r.missing_derivatives = find_missing_derivatives(cfg, conn)

    r.biggest = [
        (d["path"] or ".", d["n_photos"])
        for d in conn.execute(
            "SELECT path, n_photos FROM dirs ORDER BY n_photos DESC LIMIT ?", (sample,)
        )
        if d["n_photos"]
    ]
    return r
