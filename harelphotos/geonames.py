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
ALL_FILE = "allCountries.zip"          # 421 MB, 13.5 M rows; landmarks only

# Landmarks, and how close you must be for one to be worth mentioning.
#
# A radius per feature code, not one threshold, because the features are not
# the same size: standing 1.5 km from a museum means you are not at the museum,
# while 1.5 km from an airport means you are in the middle of it. Tested on the
# real dump against a photo taken inside Ben Gurion Airport: the nearest thing
# of any kind is a hill 1.24 km away, the nearest town 1.57 km, and the airport
# itself 1.77 km -- so nearest-wins answers "a hill", and only a radius that
# knows an airport is enormous answers "Ben Gurion Airport".
#
# The exclusions matter as much as the inclusions. Streams, churches, schools,
# hotels, road junctions and wells are millions of rows that would label a
# family photo "Saint Mary Church" -- and in that same airport test, a hotel
# 2.7 km away and two stream channels were closer than anything meaningful.
LANDMARK_RADII_M = {
    # Travel. Where a holiday actually passes through, and large enough that
    # being a kilometre from the middle still means being there.
    "AIRP": 4000, "PRT": 3000, "RSTN": 600, "MAR": 1500,
    # Built things: small, so you have to be at them.
    "MNMT": 800, "MUS": 800, "CSTL": 800, "ANS": 800, "HSTS": 800,
    "PAL": 800, "RUIN": 800, "PYR": 800, "TOWR": 800, "BDG": 800,
    "OBS": 800, "ZOO": 1200, "THTR": 800, "AMTH": 800,
    # Natural points.
    "MT": 1500, "PK": 1500, "VLC": 2500, "FLLS": 1000, "CAPE": 1500,
    # Areas you are plausibly inside.
    "PRK": 5000, "AMUS": 5000, "RESN": 5000, "RESV": 5000, "ISL": 5000,
    "LK": 3000, "LGN": 3000, "BCH": 1500, "GLCR": 5000, "FRST": 5000,
    "CNYN": 5000, "DSRT": 5000, "PLAT": 5000,
}
LANDMARK_CODES = frozenset(LANDMARK_RADII_M)

# Being inside a landmark's radius is not on its own enough to name it: a
# nature reserve 4.6 km away is "within 5 km" and still not where you are, and
# a park 200 m from the middle of Tel Aviv should not displace Tel Aviv.
#
# So a landmark wins only when it is nearer than the town, or the town is a
# small place and the landmark is barely further off. Checked against the real
# dump: an airport beats a moshav of 971 people 200 m closer; a city park does
# not beat Tel Aviv; a reserve 4.6 km out does not beat a suburb 400 m away;
# and an archaeological site 800 m away beats a village at 1.5 km.
LANDMARK_SLACK_M = 1000
LANDMARK_TOWN_POP_FLOOR = 5000

# Whether the town is worth naming alongside the landmark is really a question
# about the town. "Gan Ha'Ir, Tel Aviv" is better than either half; "Ben Gurion
# Airport, Tsafriyya" is worse than either, because nobody associates the
# airport with the moshav beside it. So a town above the population floor keeps
# its place and the landmark joins it -- but only when you are genuinely inside
# rather than a kilometre off, or every city photo acquires a park it was near.
LANDMARK_INSIDE_M = 750

# Countries whose first-level subdivision is worth printing.
#
# "Ben Gurion Airport, Central District, Israel" tells a traveller nothing --
# nobody outside the country knows the districts, and the country name already
# placed it. A state or province in a large federal country is different:
# "Sedona, Arizona, United States" is how people actually say it, and the
# country alone would be uselessly vague. So the region is printed only where
# it does that work.
REGION_COUNTRIES = frozenset({
    "US", "CA", "AU", "BR", "MX", "IN", "RU", "CN", "AR",
})
# The widest radius above; the search need never look further than this.
LANDMARK_MAX_M = max(LANDMARK_RADII_M.values())

COL_FCLASS, COL_FCODE = 6, 7

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

LANDMARK_SCHEMA = """
CREATE TABLE IF NOT EXISTS landmarks
  (name TEXT, cc TEXT, code TEXT, lat REAL, lon REAL);
CREATE INDEX IF NOT EXISTS landmarks_ll ON landmarks(lat, lon);
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


def build_landmarks(db_path: Path, cache_dir: Path | None = None, progress=None) -> int:
    """Add the landmark table to an existing geonames.sqlite.

    Streams the 421 MB worldwide dump a line at a time and keeps only the
    curated feature codes -- about a tenth of 13.5 M rows. Streaming rather
    than reading it in: the machine this runs on has a gigabyte of memory.
    """
    cache_dir = cache_dir or db_path.parent / "geonames-cache"
    if not db_path.exists():
        raise GeonamesError(
            f"{db_path} not found — run 'harelphotos init --geonames' first"
        )
    path = _fetch_to_file(ALL_FILE, cache_dir, progress=progress)

    conn = sqlite3.connect(db_path)
    kept = seen = 0
    try:
        conn.executescript(LANDMARK_SCHEMA)
        conn.execute("DELETE FROM landmarks")
        batch = []
        with zipfile.ZipFile(path) as z:
            with z.open("allCountries.txt") as fh:
                for raw in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    seen += 1
                    if progress and seen % 1_000_000 == 0:
                        progress(f"{seen // 1000:,}k rows read, {kept:,} landmarks kept")
                    f = raw.split("\t")
                    if len(f) <= COL_FCODE or f[COL_FCODE] not in LANDMARK_CODES:
                        continue
                    try:
                        lat, lon = float(f[COL_LAT]), float(f[COL_LON])
                    except ValueError:
                        continue      # the dump does contain malformed rows
                    batch.append((f[COL_NAME], f[COL_CC], f[COL_FCODE], lat, lon))
                    kept += 1
                    if len(batch) >= 20000:
                        conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)", batch)
                        batch.clear()
        if batch:
            conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)", batch)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('landmarks', ?)", (str(kept),))
        conn.commit()
    finally:
        conn.close()
    return kept


def _fetch_to_file(name: str, cache_dir: Path, progress=None) -> Path:
    """Download to disk, not into memory: this file is 421 MB."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_name(dest.name + ".part")
    url = BASE_URL + name
    if progress:
        progress(f"downloading {name} (about 421 MB)")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress and total and done % (32 << 20) < (1 << 20):
                    progress(f"downloaded {done // (1 << 20)} of {total // (1 << 20)} MB")
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise GeonamesError(f"cannot download {url}: {e}") from e
    tmp.replace(dest)
    return dest


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
        self._landmark_cache: dict[tuple[float, float], str | None] = {}
        self.has_landmarks = bool(
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='landmarks'"
            ).fetchone()
        )

    def close(self) -> None:
        self.conn.close()

    def _nearest(self, lat: float, lon: float):
        for d in self.BOXES:
            # A degree of longitude shrinks towards the poles; widen to match.
            dlon = d / max(0.05, math.cos(math.radians(lat)))
            rows = self.conn.execute(
                "SELECT name, cc, admin1, lat, lon, pop FROM places "
                "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (lat - d, lat + d, lon - dlon, lon + dlon),
            ).fetchall()
            if rows:
                best = min(rows, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))
                return best, haversine(lat, lon, best["lat"], best["lon"])
        return None, None      # mid-ocean, or a coordinate far from anywhere

    def _nearest_landmark(self, lat: float, lon: float):
        """The closest curated landmark that you are actually *at*.

        Each feature code carries its own radius, because the features are not
        the same size: a kilometre from a museum is not at the museum, but a
        kilometre from an airport is in the middle of one.
        """
        if not self.has_landmarks:
            return None
        d = LANDMARK_MAX_M / 111_320.0
        dlon = d / max(0.05, math.cos(math.radians(lat)))
        rows = self.conn.execute(
            "SELECT name, cc, code, lat, lon FROM landmarks "
            "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - d, lat + d, lon - dlon, lon + dlon),
        ).fetchall()
        best = None
        for r in rows:
            dist = haversine(lat, lon, r["lat"], r["lon"])
            if dist <= LANDMARK_RADII_M.get(r["code"], 0):
                # Closest wins among those you are genuinely within.
                if best is None or dist < best[1]:
                    best = (r, dist)
        return best

    def landmark(self, lat: float, lon: float) -> str | None:
        key = (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION))
        if key in self._landmark_cache:
            return self._landmark_cache[key]
        found = self._nearest_landmark(lat, lon)
        name = found[0]["name"] if found else None
        self._landmark_cache[key] = name
        return name

    def describe(self, lat: float, lon: float) -> tuple[str, int] | None:
        """('Náxos, Greece', 213) — the place name and distance in metres."""
        key = (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION))
        if key in self._cache:
            return self._cache[key]
        row, dist = self._nearest(lat, lon)
        result = None
        if row is not None:
            result = self._describe_row(row, dist, lat, lon)
        self._cache[key] = result
        return result

    def _describe_row(self, row, dist: float, lat: float, lon: float):
        mark = self._nearest_landmark(lat, lon)
        pop = _int(row["pop"])
        name, keep_town = None, True

        if mark is not None:
            mark_row, mark_dist = mark
            if pop >= LANDMARK_TOWN_POP_FLOOR:
                # A town people have heard of. It keeps its place and the
                # landmark joins it -- if you are actually inside the thing.
                if mark_dist <= LANDMARK_INSIDE_M:
                    name = mark_row["name"]
            elif mark_dist <= dist + LANDMARK_SLACK_M:
                # A hamlet, a moshav, a suburb. The landmark is the useful
                # half, and the town would only add noise.
                name, keep_town = mark_row["name"], False

        parts = [p for p in (name, row["name"] if keep_town else None) if p]
        region = self._admin1.get(f"{row['cc']}.{row['admin1']}")
        country = self._countries.get(row["cc"], row["cc"])
        # A region earns its place only when it is not repeating what is
        # already there ("Tel Aviv, Tel Aviv, Israel" reads badly), and not
        # when a landmark and a town are already two names deep.
        if (region and row["cc"] in REGION_COUNTRIES
                and len(parts) < 2 and region not in parts):
            parts.append(region)
        if country:
            parts.append(country)
        # The distance decides whether this reads as "near X". A landmark is
        # only ever claimed from inside its own radius, so it is never "near".
        return (", ".join(parts), 0 if name else int(dist))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def format_place(place: str | None, dist_m: int | None) -> str | None:
    """Render for display, hedging when the nearest place is far away."""
    if not place:
        return None
    if dist_m is not None and dist_m > NEAR_THRESHOLD_M:
        return f"near {place} ({dist_m / 1000:.0f} km)"
    return place
