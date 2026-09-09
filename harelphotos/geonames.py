"""Offline reverse geocoding (DESIGN.md 9.5).

Turns a photo's GPS coordinates into "Náxos, Greece", from a local dataset —
no network call at scan time and, more to the point, no transmitting the
coordinates of every photo your family has ever taken (including your house) to
a third-party geocoding service.

Measured on the real dataset: 235,694 places, a 16 MB SQLite table built in
under a second, 0.16 ms per distinct lookup, and 0.7 s for an 80,000-photo
collection once coordinates are rounded and memoised.
"""

from __future__ import annotations

import io
import logging
import math
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("harelphotos.geonames")

BASE_URL = "https://download.geonames.org/export/dump/"
CITIES_FILE = "cities500.zip"          # populated places with 500+ inhabitants
COUNTRY_FILE = "countryInfo.txt"
ADMIN1_FILE = "admin1CodesASCII.txt"

# Columns we use from the 19-column, tab-separated dump.
COL_NAME, COL_LAT, COL_LON, COL_CC, COL_ADMIN1, COL_POP = 1, 4, 5, 8, 10, 14

# Rounding coordinates to three decimals (~100 m) is what collapses tens of
# thousands of photos into a few thousand distinct lookups.
CACHE_PRECISION = 3

# Beyond this, "near X" is more honest than "X".
NEAR_THRESHOLD_M = 5000

SCHEMA = """
CREATE TABLE places (name TEXT, cc TEXT, admin1 TEXT, lat REAL, lon REAL, pop INTEGER);
CREATE INDEX places_ll ON places(lat, lon);
CREATE TABLE countries (cc TEXT PRIMARY KEY, name TEXT);
CREATE TABLE admin1 (key TEXT PRIMARY KEY, name TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


class GeonamesError(Exception):
    pass


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _fetch(name: str, cache_dir: Path) -> bytes:
    dest = cache_dir / name
    if dest.exists():
        log.info("using cached %s", dest)
        return dest.read_bytes()
    url = BASE_URL + name
    log.info("downloading %s", url)
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
    except Exception as e:
        raise GeonamesError(f"cannot download {url}: {e}") from e
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def build(db_path: Path, cache_dir: Path | None = None, progress=None) -> int:
    """Download the dataset and build geonames.sqlite. Returns place count."""
    cache_dir = cache_dir or db_path.parent / "geonames-cache"
    cities_zip = _fetch(CITIES_FILE, cache_dir)
    countries = _fetch(COUNTRY_FILE, cache_dir).decode("utf-8", "replace")
    admin1 = _fetch(ADMIN1_FILE, cache_dir).decode("utf-8", "replace")

    if progress:
        progress("parsing")
    with zipfile.ZipFile(io.BytesIO(cities_zip)) as z:
        text = z.read("cities500.txt").decode("utf-8", "replace")

    rows = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) <= COL_POP:
            continue
        try:
            lat, lon = float(f[COL_LAT]), float(f[COL_LON])
        except ValueError:
            continue          # the dump does contain a few malformed rows
        try:
            pop = int(f[COL_POP] or 0)
        except ValueError:
            pop = 0
        rows.append((f[COL_NAME], f[COL_CC], f[COL_ADMIN1], lat, lon, pop))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO places VALUES (?,?,?,?,?,?)", rows)
        conn.executemany(
            "INSERT OR REPLACE INTO countries VALUES (?,?)",
            [
                (p[0], p[4])
                for p in (ln.split("\t") for ln in countries.splitlines()
                          if ln and not ln.startswith("#"))
                if len(p) > 4
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO admin1 VALUES (?,?)",
            [
                (p[0], p[1])
                for p in (ln.split("\t") for ln in admin1.splitlines() if ln)
                if len(p) > 1
            ],
        )
        conn.execute("INSERT INTO meta VALUES ('source', ?)", (BASE_URL + CITIES_FILE,))
        conn.execute("INSERT INTO meta VALUES ('places', ?)", (str(len(rows)),))
        conn.commit()
    finally:
        conn.close()
    tmp.replace(db_path)
    return len(rows)


class Geocoder:
    """Nearest-populated-place lookup over the local dataset.

    No k-d tree and no numpy: an indexed bounding-box query that widens
    geometrically, then an exact great-circle distance over the handful of
    candidates it returns.
    """

    # Degrees of latitude; the first box covers ~17 km and almost always hits.
    BOXES = (0.15, 0.6, 2.5, 10.0)

    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise GeonamesError(
                f"{db_path} not found — run 'harelphotos init --geonames' first"
            )
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self._countries = {
            r["cc"]: r["name"] for r in self.conn.execute("SELECT cc, name FROM countries")
        }
        self._admin1 = {
            r["key"]: r["name"] for r in self.conn.execute("SELECT key, name FROM admin1")
        }
        self._cache: dict[tuple[float, float], tuple[str, int] | None] = {}

    def close(self) -> None:
        self.conn.close()

    def _nearest(self, lat: float, lon: float):
        for d in self.BOXES:
            # A degree of longitude shrinks towards the poles; widen to match.
            dlon = d / max(0.05, math.cos(math.radians(lat)))
            rows = self.conn.execute(
                "SELECT name, cc, admin1, lat, lon FROM places "
                "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (lat - d, lat + d, lon - dlon, lon + dlon),
            ).fetchall()
            if rows:
                best = min(rows, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))
                return best, haversine(lat, lon, best["lat"], best["lon"])
        return None, None      # mid-ocean, or a coordinate far from anywhere

    def describe(self, lat: float, lon: float) -> tuple[str, int] | None:
        """('Náxos, Greece', 213) — the place name and distance in metres."""
        key = (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION))
        if key in self._cache:
            return self._cache[key]
        row, dist = self._nearest(lat, lon)
        result = None
        if row is not None:
            parts = [row["name"]]
            region = self._admin1.get(f"{row['cc']}.{row['admin1']}")
            country = self._countries.get(row["cc"], row["cc"])
            # A region only earns its place when it is not just the city again
            # ("Tel Aviv, Tel Aviv, Israel" reads badly).
            if region and region != row["name"]:
                parts.append(region)
            if country:
                parts.append(country)
            result = (", ".join(parts), int(dist))
        self._cache[key] = result
        return result


def format_place(place: str | None, dist_m: int | None) -> str | None:
    """Render for display, hedging when the nearest place is far away."""
    if not place:
        return None
    if dist_m is not None and dist_m > NEAR_THRESHOLD_M:
        return f"near {place} ({dist_m / 1000:.0f} km)"
    return place
