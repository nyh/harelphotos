"""Every place name we have ever argued about, against the real dumps.

The rest of the geocoding tests build a small world by hand, with the distances
and populations measured from the real data. That is fast and offline and it
has a weakness worth being honest about: **each fixture encodes what I believed
was near a coordinate.** A feature I did not know about is a feature the test
cannot see, so the test passes and the photograph is still labelled wrongly.

That is not hypothetical. "H̱orbat Tsohara" was reported as a bad caption; the
data turned out to hold a *second* ruin 335 m away in the other direction,
which no hand-built fixture had in it.

So this file asks the real question: given the files GeoNames actually
publishes, what does a photograph at these coordinates end up called? Every
case below is a real photograph from a real collection, and most of them were a
wrong answer once.

Skipped unless you ask for it::

    HARELPHOTOS_GEONAMES_LIVE=1 pytest tests/test_geonames_live.py

because it downloads about 80 MB the first time -- `cities500`, which is what
production builds its places from, and the per-country dumps for the two
countries these photographs are in. The worldwide landmark file is 402 MB and
is not needed: restricted to a country, the per-country dump holds the same
rows, and they go through `geonames.landmark_row`, the same filter production
uses.

Cached in ``HARELPHOTOS_GEONAMES_CACHE`` if set, else a directory under the
system temporary directory, so the download happens once per machine.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import sqlite3
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import pytest

from harelphotos import geonames

pytestmark = pytest.mark.skipif(
    os.environ.get("HARELPHOTOS_GEONAMES_LIVE") != "1",
    reason="set HARELPHOTOS_GEONAMES_LIVE=1 (downloads ~80 MB, cached)",
)

COUNTRIES = ("IL", "US", "DE")


def cache_dir() -> Path:
    d = os.environ.get("HARELPHOTOS_GEONAMES_CACHE")
    path = Path(d) if d else Path(tempfile.gettempdir()) / "harelphotos-geonames"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch(name: str, cache: Path) -> Path:
    dest = cache / name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(geonames.BASE_URL + name, timeout=120) as r, \
            open(tmp, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)
    return dest


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """A real places table and a real landmarks table, in one database."""
    cache = cache_dir()
    path = tmp_path_factory.mktemp("live") / "geonames.sqlite"

    # The country names and region names production uses.
    for name in (geonames.COUNTRY_FILE, geonames.ADMIN1_FILE):
        fetch(name, cache)
    geonames.build(path, cache_dir=cache)

    # Places *and* landmarks from the per-country dumps rather than the 402 MB
    # worldwide one, each through production's own row filter. Places are
    # rebuilt rather than added to, because `build` above filled them from
    # cities500 and this file exists to test what `--villages` gives.
    conn = sqlite3.connect(path)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.execute("DELETE FROM landmarks")
    conn.execute("DELETE FROM places")
    for cc in COUNTRIES:
        zip_path = fetch(f"{cc}.zip", cache)
        with zipfile.ZipFile(zip_path) as z, z.open(f"{cc}.txt") as fh:
            places, marks = [], []
            for raw in fh:
                f = raw.decode("utf-8", "replace").split("\t")
                row = geonames.place_row(f)
                if row is not None:
                    places.append(row)
                row = geonames.landmark_row(f)
                if row is not None:
                    marks.append(row)
            conn.executemany(
                "INSERT INTO places (name, cc, admin1, lat, lon, pop, code) "
                "VALUES (?,?,?,?,?,?,?)", places)
            conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)", marks)
    conn.commit()
    conn.close()
    return path


# Every coordinate here is from a photograph in the collection this software
# was written for. "was" records what it used to come back as, when that is why
# the case exists -- those are the regressions this file is here to catch.
CASES = [
    # ---------------------------------------------------------------- Israel
    ("a village on a mountainside", 32.8521917, 35.2363889,
     "Manof, Israel",
     "was Har Shekhanya: a hill with three villages on it, naming itself"),
    ("a restaurant in a small town", 32.836843, 35.248091,
     "Kaukab Abū el Hījā, Israel",
     "was Shmurat Me‘arat Shekhanya, a reserve 509 m off"),
    ("between two towns", 32.921538, 35.308403,
     "Karmi’el, Israel",
     "was Naḥf, 127 m nearer but not the town the camera was inside"),
    ("beside two anonymous ruins", 32.918251, 35.297912,
     "Karmi’el, Israel",
     "was H̱orbat Tsohara, 346 m away, with a second ruin 335 m off"),
    ("inside the Haifa zoo", 32.8062458, 34.9850178,
     "Haifa, Israel",
     "correct, and the best available: GeoNames has no record of the zoo"),

    # ----------------------------------------- not photographs, but regressions
    #
    # Two places that have gone wrong before and are cheap to keep watching.
    ("a resort with no town inside it", 28.3852, -81.5639,
     "Walt Disney World Resort, Florida, United States",
     "was Celebration, a town 7 km away: Bay Lake has 50 people and so is "
     "not in cities500 at all, leaving the nearest *recorded* place large "
     "enough to take the branch where a landmark needs to be within 750 m"),
    ("a fairground you are not at", 32.1040, 34.8110,
     "Tel Aviv, Israel",
     "400 m from Luna Park is not at Luna Park, so the shrink has to fire -- "
     "which needs the built-up test to ignore the neighborhood rows with no "
     "population that sit nearer than any town"),
    ("a fairground you are at", 32.10666, 34.81265,
     "Tel Aviv Luna Park, Tel Aviv, Israel",
     "and the city is Tel Aviv, which is where Luna Park is. Ramat Gan's "
     "centre is 2.7 km away against Tel Aviv's 4.2, so nearest-wins said "
     "Ramat Gan until the villages arrived"),

    # ------------------------------------------------------- the Black Forest
    #
    # Three photographs from one apartment, which came back as three different
    # places because the village they are in was not in the table at all.
    ("an apartment in a village, 1", 47.857021, 7.9163261,
     "Muggenbrunn, Germany", "was Köpfle, a hill 1.3 km off"),
    ("an apartment in a village, 2", 47.8548144, 7.9212054,
     "Muggenbrunn, Germany", "was Glashof Wald, a forest 3.7 km off"),
    ("an apartment in a village, 3", 47.8547626, 7.9219531,
     "Muggenbrunn, Germany",
     "was Todtnau, 3.3 km off -- nine metres of GPS drift from the one above "
     "flipped which distant town was nearest, and the two fell on opposite "
     "sides of the population floor"),

    ("inside a great park beside a village, 1", 48.2674837, 7.7223956,
     "Europa-Park, Germany", "814 m from Rust's center"),
    ("inside a great park beside a village, 2", 48.2671905, 7.7234762,
     "Europa-Park, Germany",
     "80 m from the one above and 732 m from Rust's center, whose estimated "
     "extent is 755 -- so the two sat either side of it and came back "
     "\"Europa-Park\" and \"Rust\""),

    # -------------------------------------------- and what must not come back
    ("a city, not its neighborhood", 42.3589889, -71.0506944,
     "Christopher Columbus Park, Boston, Massachusetts, United States",
     "North End has a second row with no population, which villages let in"),
    ("a village inside a city keeps its name", 42.3119, -71.226175,
     "Newton Upper Falls, Massachusetts, United States",
     "Newton is 11.7x its population, under the dominance threshold"),
]

# Luna Park used to be here, under "known wrong and not fixable from this
# data": it is in Tel Aviv, and with only the towns in the table the nearest
# centre was Ramat Gan's, 2.7 km against Tel Aviv's 4.2. Adding the villages
# fixed it without anybody aiming at it. The neighborhood rows nearest the park
# carry no population, which makes the dominance rule the one that decides, and
# it picks the largest city whose extent reaches -- Tel Aviv. It cannot misfire
# where the nearest place *has* a population: at Newton Upper Falls that puts
# the threshold at 151,580 and Newton's 88,817 is nowhere near it.


@pytest.mark.parametrize("label,lat,lon,expected,note", CASES,
                         ids=[c[0].replace(" ", "_") for c in CASES])
def test_a_real_photograph_is_named_correctly(db, label, lat, lon, expected, note):
    gc = geonames.Geocoder(db)
    try:
        found = gc.describe(lat, lon)
    finally:
        gc.close()
    got = found[0] if found else None
    assert got == expected, f"{label}: {got!r} (expected {expected!r}; {note})"
