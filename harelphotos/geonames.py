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
    # Travel. Large, and where a holiday actually passes through.
    # 2.5 km, not more: it now overrides the town outright, and a bigger
    # circle around a city airport would swallow the neighbourhoods beside it.
    # Measured: 2.03 km from the middle of Boston Logan while in its terminal,
    # 1.77 km from Ben Gurion's while in that one.
    "AIRP": 2500, "PRT": 3000, "MAR": 1500, "RSTN": 400,

    # Built things, by how big the thing physically is -- not by how famous.
    # A bridge you are either on or not: 800 m put a photo taken indoors 793 m
    # away at "Echo Bridge", which is a quarter of a mile of somebody's town
    # between them. A dig or a battlefield really is that wide.
    # Things you have to be standing at. Worth having -- a dam or a mast you
    # walked up to is a good caption -- and worthless at any distance.
    # No TOWR. It is 16,579 rows in the United States and almost all of them
    # are broadcast masts -- "WBUR-FM (Boston)" is not a caption anybody wants,
    # and seven of them stood 521 m from one of the photos that prompted this
    # work. Nothing famous is lost: the Eiffel Tower is filed as MNMT, and TOWR
    # in France is 235 old stone towers.
    "DAM": 120, "BDG": 150, "LTHSE": 150,
    # A shopping centre is a place a holiday actually spends an afternoon, and
    # you are inside one or you are not. Found while looking at why a photo
    # taken in a shop was captioned with a pond: the mall was 54 m away and not
    # in the list at all.
    "MALL": 250,
    "MNMT": 250, "MUS": 250, "THTR": 250,
    "OBS": 300, "AMTH": 300,
    "FLLS": 400,
    "CSTL": 500, "PAL": 500, "RUIN": 500,
    "ZOO": 600, "PYR": 600,
    "ANS": 800, "HSTS": 800,

    # Natural points. The recorded point is the summit, and the mountain
    # beneath it is much wider than that.
    "PK": 1000, "MT": 1500, "CAPE": 1500, "VLC": 2500,

    # Areas you are plausibly inside.
    # A pond you are standing at, not one across the neighbourhood. The
    # recorded point is the centroid, which for a small pond is the pond and
    # for a great lake is open water no photograph is taken from, so a wide
    # radius here buys nothing and cost a shop photo its name.
    "BCH": 1500, "LK": 500, "LGN": 500,
    "PRK": 5000, "AMUS": 5000, "RESN": 5000, "RESV": 5000, "ISL": 5000,
    "GLCR": 5000, "FRST": 5000, "CNYN": 5000, "DSRT": 5000, "PLAT": 5000,
}
LANDMARK_CODES = frozenset(LANDMARK_RADII_M)

# Area features whose generous radius only holds out in the open.
#
# GeoNames files a national park and a neighbourhood ballfield under the same
# code, PRK, with nothing to tell them apart -- and in the United States it
# files National Register "historic districts" there too. So a photo taken
# indoors was captioned "Newton Upper Falls Historic District", 426 m away,
# because it inherited a radius meant for Yellowstone.
#
# A town nearby is the signal that settles it: a wilderness five kilometres
# across does not have a village 600 m from the middle of it. When there is a
# town close by, these shrink to something you have to be inside.
#
# Deliberately not AMUS: a theme park is genuinely large and deliberately built
# next to a town, so Walt Disney World would lose its name to Celebration,
# Florida. Nor AIRP, for the same reason -- that is the whole point of it.
# AMUS belongs here for the same reason PRK does: it covers Walt Disney World,
# a hundred square kilometres, and Tel Aviv's Luna Park, a hundred metres
# across. What separates them is not the code but the neighbourhood -- so the
# shrink applies only when a town of real size is close by. Disney's nearest
# neighbour is a company town of fifty people; Luna Park's is a city of
# 432,000, and a fairground does not get to displace that city from two
# kilometres away.
AREA_SHRINK_CODES = frozenset({
    "PRK", "AMUS", "RESN", "RESV", "FRST", "CNYN", "DSRT", "PLAT", "GLCR",
    "ISL", "LK", "LGN", "BCH",
})
AREA_TOWN_NEAR_M = 2000
AREA_SHRUNK_M = 250

# Places you are overwhelmingly likely to be *inside* rather than beside.
#
# A hamlet next to one of these is nominally closer and almost never where the
# photograph was taken: a tourist at Ben Gurion Airport is in the airport, not
# in the moshav 200 m nearer, and a photograph at Walt Disney World is not in
# Bay Lake, population 50, which happens to sit inside the resort 1.6 km closer
# than the resort's own centre. So these beat a small town whatever the
# distances say, as long as you are within their radius at all.
DESTINATION_CODES = frozenset({"AIRP", "AMUS", "PRT"})

# GeoNames marks features that no longer exist by putting "(historical)" in the
# name, and keeps them. "Wood Island Park (historical)" is a park that was
# demolished to build Boston's airport, and it captioned a photo taken in that
# airport's terminal. There are 123,855 such rows in the United States alone.
HISTORICAL_MARK = "(historical)"


def codes_fingerprint() -> str:
    """Identifies the allowlist a landmark table was filtered with.

    The table is filtered at build time, so adding a feature code does nothing
    for a collection that already built one -- the rows were never kept. That
    bit once already: a shopping centre was added to the list, and a photo
    taken inside one went on naming a pond, because re-running `geocode` cannot
    conjure rows the build discarded.
    """
    import hashlib

    joined = ",".join(sorted(LANDMARK_CODES))
    return hashlib.blake2b(joined.encode(), digest_size=8).hexdigest()

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
    curated feature codes. Measured against the real per-country dumps that is
    9% of France, 15% of the United States and 25% of Israel -- roughly 2 M
    rows worldwide and about 250 MB once indexed. The proportion says more
    about how densely a country is mapped than about the allowlist.

    Streaming rather than reading it in: the machine this runs on has a
    gigabyte of memory.
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
                    if HISTORICAL_MARK in f[COL_NAME]:
                        continue      # demolished, drained, or renamed away
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
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('landmark_codes', ?)",
                     (codes_fingerprint(),))
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
        # Whether the table was filtered with the allowlist this version uses.
        built = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'landmark_codes'"
        ).fetchone()
        self.landmarks_stale = bool(
            self.has_landmarks
            and (built is None or built["value"] != codes_fingerprint())
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

    def _landmark_candidates(self, lat: float, lon: float, town_dist: float | None,
                             town_pop: int = 0):
        """Every curated landmark whose own radius you are inside.

        A radius per feature code, because the features are not the same size:
        a kilometre from a museum is not at the museum, but a kilometre from an
        airport is in the middle of one. And an area feature with a town beside
        it is not the wilderness its code allows for (AREA_SHRINK_CODES).
        """
        if not self.has_landmarks:
            return []
        d = LANDMARK_MAX_M / 111_320.0
        dlon = d / max(0.05, math.cos(math.radians(lat)))
        rows = self.conn.execute(
            "SELECT name, cc, code, lat, lon FROM landmarks "
            "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - d, lat + d, lon - dlon, lon + dlon),
        ).fetchall()
        # A *substantial* town nearby, not merely any hamlet.
        near_town = (town_dist is not None and town_dist <= AREA_TOWN_NEAR_M
                     and town_pop >= LANDMARK_TOWN_POP_FLOOR)
        out = []
        for r in rows:
            if HISTORICAL_MARK in r["name"]:
                continue          # demolished, drained, or renamed away
            dist = haversine(lat, lon, r["lat"], r["lon"])
            radius = LANDMARK_RADII_M.get(r["code"], 0)
            if near_town and r["code"] in AREA_SHRINK_CODES:
                radius = min(radius, AREA_SHRUNK_M)
            if dist <= radius:
                out.append((r, dist, radius))
        return out

    @staticmethod
    def _pick(candidates):
        """A destination outranks whatever else you happen to be beside.

        Inside Boston's airport the nearest curated things are a beach and a
        park a few hundred metres off and the airport two kilometres away --
        and the airport is where the photograph was taken.
        """
        if not candidates:
            return None
        return min(candidates,
                   key=lambda c: (c[0]["code"] not in DESTINATION_CODES, c[1]))

    def landmark(self, lat: float, lon: float) -> str | None:
        key = (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION))
        if key in self._landmark_cache:
            return self._landmark_cache[key]
        found = self._pick(self._landmark_candidates(lat, lon, None))
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
        pop = _int(row["pop"])
        candidates = self._landmark_candidates(lat, lon, town_dist=dist, town_pop=pop)
        name, keep_town = None, True

        airports = [c for c in candidates if c[0]["code"] == "AIRP"]
        if airports:
            # An airport replaces the town whatever the town's size. A tourist
            # inside one is in the airport, not in the neighbourhood of 15,741
            # people whose edge it happens to touch -- nor in the moshav of 971
            # that is 200 m nearer than the runway.
            name, keep_town = min(airports, key=lambda c: c[1])[0]["name"], False
        elif pop < LANDMARK_TOWN_POP_FLOOR:
            # A hamlet, a moshav, a suburb: the landmark is the useful half and
            # the town would only add noise. Still has to be roughly as close
            # as the town, unless it is a destination -- otherwise a nature
            # reserve 4.6 km from central Haifa displaces the suburb you are
            # standing in, which was wrong before.
            near = [c for c in candidates
                    if c[0]["code"] in DESTINATION_CODES
                    or c[1] <= dist + LANDMARK_SLACK_M]
            best = self._pick(near)
            if best is not None:
                name, keep_town = best[0]["name"], False
        else:
            # A town people have heard of keeps its place, and the landmark
            # joins it only from genuinely inside -- which for a small thing is
            # closer than the blanket threshold: 750 m from a theatre is not at
            # the theatre, and an amusement park across a city does not get to
            # displace the city.
            inside = [c for c in candidates
                      if c[1] <= min(LANDMARK_INSIDE_M, c[2])]
            best = self._pick(inside)
            if best is not None:
                name = best[0]["name"]

        parts = [p for p in (name, row["name"] if keep_town else None) if p]
        region = self._admin1.get(f"{row['cc']}.{row['admin1']}")
        country = self._countries.get(row["cc"], row["cc"])
        # Always, in a country whose subdivisions people use: "Orient Heights,
        # United States" is a poor answer when Massachusetts is what places it.
        if region and row["cc"] in REGION_COUNTRIES and region not in parts:
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
