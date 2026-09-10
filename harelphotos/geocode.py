"""Resolving place names for indexed photos (DESIGN.md 9.5).

A pure database operation: the GPS coordinates are already in the index after
the header pass, so this never opens a photo file. Running it is seconds, not a
re-read of 300 GB.
"""

from __future__ import annotations

import json
import time
import logging
import sqlite3
from dataclasses import dataclass

from .config import Config
from .geonames import Geocoder

log = logging.getLogger("harelphotos.geocode")


@dataclass
class GeocodeStats:
    considered: int = 0
    resolved: int = 0
    unresolved: int = 0
    distinct_lookups: int = 0
    elapsed: float = 0.0
    landmarks_stale: bool = False

    def summary(self) -> str:
        if not self.considered:
            return "no photos with GPS coordinates to resolve"
        return (
            f"{self.considered:,} photos with GPS, {self.resolved:,} resolved "
            f"({self.distinct_lookups:,} distinct locations), "
            f"{self.unresolved:,} unresolved"
            + (f" in {self.elapsed:.1f}s" if self.elapsed else "")
        )


def geocode(
    cfg: Config, conn: sqlite3.Connection, *, force: bool = False, progress=None
) -> GeocodeStats:
    stats = GeocodeStats()
    sql = (
        "SELECT id, exif_json FROM photos "
        "WHERE exif_json IS NOT NULL AND exif_json LIKE '%\"lat\"%'"
    )
    if not force:
        sql += " AND place IS NULL"
    rows = conn.execute(sql).fetchall()
    if not rows:
        return stats

    gc = Geocoder(cfg.state_dir / "geonames.sqlite")
    stats.landmarks_stale = gc.landmarks_stale
    started = time.monotonic()
    try:
        for n, row in enumerate(rows, 1):
            try:
                meta = json.loads(row["exif_json"])
                lat, lon = float(meta["lat"]), float(meta["lon"])
            except (ValueError, TypeError, KeyError):
                continue
            stats.considered += 1
            found = gc.describe(lat, lon)
            if found is None:
                stats.unresolved += 1
                continue
            place, dist = found
            stats.resolved += 1
            # The landmark on its own as well as inside `place`, so the index
            # records which photos are at one without parsing the string back.
            mark = gc.landmark(lat, lon) if gc.has_landmarks else None
            conn.execute(
                "UPDATE photos SET place = ?, place_dist = ?, landmark = ? WHERE id = ?",
                (place, dist, mark, row["id"]),
            )
            if n % 500 == 0:
                conn.commit()
            # Reported every row, not every five hundred: the caller decides
            # how often to draw, on a clock rather than a count. A count-based
            # interval shows nothing at all on a small collection and stalls
            # visibly on a slow one.
            if progress:
                progress(n, len(rows))
        stats.distinct_lookups = len(gc._cache)
    finally:
        gc.close()
    conn.commit()
    if progress:
        progress(len(rows), len(rows))
    stats.elapsed = time.monotonic() - started
    return stats
