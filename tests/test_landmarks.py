"""Landmark naming (DESIGN.md 9.5), the opt-in second layer over the cities.

**Getting the town right matters more than getting the landmark right.** A
photograph labelled with the wrong city, or with a hospital record or a
neighborhood in place of one, is a bad answer; a correct city with a garden or
a marker also mentioned is a slightly noisy one. So the tests below that assert
a *town* -- Boston rather than "North End" or "VA Boston Healthcare System",
Newton Upper Falls rather than Newton -- are the ones to keep working, and the
landmark rules exist to avoid captioning a photo with something absurd rather
than to find the perfect name.

Every test here uses coordinates, distances and populations measured in the
real GeoNames dumps, because each one was a wrong answer first. GeoNames has no
notion of significance and records no extent, so one feature code covers a
hundred square kilometres and a hundred metres alike -- most of these rules are
a signal standing in for size.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sqlite3

import pytest

from harelphotos import geonames


@pytest.fixture
def db(tmp_path):
    """A tiny world: one big city, one hamlet, an airport, a park, a reserve."""
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.executemany("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES (?,?,?,?,?,?)", [
        ("Bigtown",  "XX", "01", 32.0000, 34.0000, 400_000),
        ("Hamlet",   "XX", "01", 33.0000, 34.0000, 900),
        ("Suburb",   "XX", "01", 34.0000, 34.0000, 0),
    ])
    conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)", [
        # 200 m from the middle of Bigtown.
        ("Central Park", "XX", "PRK", 32.0018, 34.0000),
        # The real geometry that prompted this: standing between a moshav
        # 1.57 km away and an airport 1.77 km away, i.e. inside the airport.
        ("Intl Airport", "XX", "AIRP", 33.0300, 34.0000),
        # 4.6 km from Suburb: inside PRK's 5 km radius, nowhere near it.
        ("Far Reserve",  "XX", "RESN", 34.0413, 34.0000),
    ])
    conn.execute("INSERT INTO countries VALUES ('XX','Exampleland')")
    conn.execute("INSERT INTO admin1 VALUES ('XX.01','Example Region')")
    # ... and a federal country, where the state IS worth printing.
    conn.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES ('Sedona','US','AZ',34.87,-111.76,10000)")
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.AZ','Arizona')")
    conn.commit()
    conn.close()
    return path


def name_at(db, lat, lon):
    gc = geonames.Geocoder(db)
    try:
        got = gc.describe(lat, lon)
        return got[0] if got else None
    finally:
        gc.close()


def test_an_airport_replaces_the_hamlet_beside_it(db):
    """The case that prompted all of this. A photo taken inside Ben Gurion
    Airport was labelled with a moshav of 971 people 200 m nearer than the
    airport, and nobody associates the airport with that moshav."""
    got = name_at(db, 33.0141, 34.0000)
    assert got.startswith("Intl Airport"), got
    assert "Hamlet" not in got


def test_a_park_joins_a_real_city_rather_than_replacing_it(db):
    """"Central Park, Bigtown" is better than either half alone -- unlike the
    airport, where the nearby place adds nothing."""
    got = name_at(db, 32.0000, 34.0000)
    assert got.startswith("Central Park"), got
    assert "Bigtown" in got


def test_a_landmark_kilometres_away_is_not_claimed(db):
    """Inside a 5 km radius is not the same as being there."""
    got = name_at(db, 34.0000, 34.0000)
    assert "Far Reserve" not in got
    assert got.startswith("Suburb"), got


def test_a_city_photo_is_not_hijacked_by_a_park_it_is_merely_near(db):
    """A kilometre from the park, still in the city: the park has not earned
    a mention."""
    got = name_at(db, 32.0100, 34.0000)      # ~900 m from the park's center
    assert "Central Park" not in got
    assert got.startswith("Bigtown"), got


def test_without_the_landmark_table_nothing_changes(tmp_path):
    """`init --landmarks` is opt-in; a collection that skipped it must behave
    exactly as before rather than erroring."""
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES ('Bigtown','XX','01',32.0,34.0,400000)")
    conn.execute("INSERT INTO countries VALUES ('XX','Exampleland')")
    conn.commit(); conn.close()

    gc = geonames.Geocoder(path)
    try:
        assert gc.has_landmarks is False
        assert gc.landmark(32.0, 34.0) is None
        assert gc.describe(32.0, 34.0)[0].startswith("Bigtown")
    finally:
        gc.close()


def test_the_curated_codes_exclude_the_noise(db):
    """Streams, churches, schools, hotels and wells are millions of rows that
    would label a family photo "Saint Mary Church"."""
    for noisy in ("STM", "CH", "SCH", "BLDG", "HTL", "PO", "WLL", "HLL", "RDJCT"):
        assert noisy not in geonames.LANDMARK_CODES, noisy
    for wanted in ("AIRP", "PRK", "MNMT", "MUS", "MT"):
        assert wanted in geonames.LANDMARK_CODES, wanted


def test_an_airport_gets_a_far_wider_radius_than_a_museum(db):
    """A kilometre from a museum is not at the museum; a kilometre from an
    airport is in the middle of one."""
    # 2.5 km, not more: an airport overrides the town outright, so a bigger
    # circle around a city airport would swallow the neighborhoods beside it.
    assert 2000 < geonames.LANDMARK_RADII_M["AIRP"] <= 3000
    assert geonames.LANDMARK_RADII_M["MUS"] < 1000


def test_a_district_nobody_knows_is_left_out(db):
    """"Central District, Israel" tells a traveller nothing the country name
    had not already said."""
    got = name_at(db, 32.0000, 34.0000)
    assert "Example Region" not in got, got
    assert got.endswith("Exampleland")


def test_a_state_in_a_federal_country_is_kept(db):
    """"Sedona, Arizona, United States" is how people actually say it, and the
    country alone would be uselessly vague."""
    got = name_at(db, 34.87, -111.76)
    assert got == "Sedona, Arizona, United States", got


def test_a_bridge_does_not_reach_across_a_town(tmp_path):
    """A photo taken indoors, 793 m from Echo Bridge, was labelled with the
    bridge. That is a quarter of a mile of somebody's town in between.

    The radius has to follow how physically big the thing is, not how famous:
    a bridge you are either on or you are not, while an airport 1.8 km away is
    still all around you.
    """
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) "
                 "VALUES ('Newton Upper Falls','US','MA',42.3119,-71.2262,9000)")
    conn.execute("INSERT INTO landmarks VALUES "
                 "('Echo Bridge','US','BDG',42.3167,-71.2333)")
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.MA','Massachusetts')")
    conn.commit(); conn.close()

    got = name_at(path, 42.3119, -71.226175)
    assert "Echo Bridge" not in got, got
    assert got == "Newton Upper Falls, Massachusetts, United States", got


def test_radii_follow_physical_size(db):
    """The ordering that keeps the rest honest."""
    r = geonames.LANDMARK_RADII_M
    assert r["BDG"] < r["MUS"] < r["CSTL"] < r["ANS"] < r["MT"] < r["AIRP"] <= r["PRK"]


# --------------------------------------------- geometry taken from real data
#
# Each of these reproduces the distances and populations measured in the actual
# GeoNames dumps, because every one of them was a wrong answer first.

def _world(tmp_path, places, landmarks):
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.executemany("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES (?,?,?,?,?,?)", places)
    conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)", landmarks)
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.MA','Massachusetts')")
    conn.execute("INSERT INTO admin1 VALUES ('US.FL','Florida')")
    conn.commit(); conn.close()
    return path


def test_a_historic_district_does_not_caption_a_photo_taken_indoors(tmp_path):
    """GeoNames files National Register historic districts under PRK, the same
    code as Yellowstone, so a photo of a dog indoors was captioned "Newton
    Upper Falls Historic District" 426 m away. A town 600 m off is the signal
    that this is not a five-kilometre wilderness."""
    path = _world(
        tmp_path,
        [("Newton Upper Falls", "US", "MA", 42.3173, -71.2262, 9000)],
        [("Newton Upper Falls Historic District", "US", "PRK", 42.3157, -71.2262),
         ("Mills Field", "US", "PRK", 42.3168, -71.2262)],
    )
    got = name_at(path, 42.3119, -71.226175)
    assert "Historic District" not in got, got
    assert "Mills Field" not in got, got
    assert got.startswith("Newton Upper Falls"), got


def test_a_park_out_in_the_open_keeps_its_generous_radius(tmp_path):
    """The shrink must not cost a real national park its name: the signal is a
    town nearby, and a wilderness does not have one."""
    path = _world(
        tmp_path,
        [("Faraway", "US", "MA", 44.0000, -71.0000, 800)],     # 100+ km off
        [("Big Wilderness", "US", "PRK", 42.3200, -71.2262)],
    )
    got = name_at(path, 42.3119, -71.226175)                    # ~900 m away
    assert got.startswith("Big Wilderness"), got


def test_a_resort_beats_the_hamlet_inside_it(tmp_path):
    """Bay Lake, population 50, sits inside Walt Disney World 1.6 km nearer
    than the resort's own center. A tourist there means the resort."""
    path = _world(
        tmp_path,
        [("Bay Lake", "US", "FL", 28.3891, -81.5639, 50)],          # 429 m
        [("Walt Disney World Resort", "US", "AMUS", 28.4034, -81.5639)],  # 2028 m
    )
    got = name_at(path, 28.3852, -81.5639)
    assert got.startswith("Walt Disney World Resort"), got
    assert "Bay Lake" not in got


def test_a_thing_you_must_stand_at_is_only_named_from_beside_it(tmp_path):
    """A dam is a good caption at twenty metres and meaningless at five
    hundred. (Masts were in this list too and are not any more -- see
    test_broadcast_masts_are_not_landmarks.)"""
    path = _world(
        tmp_path,
        [("Sometown", "US", "MA", 42.3119, -71.2262, 9000)],
        [("Mill Dam", "US", "DAM", 42.3166, -71.2262)],             # ~520 m
    )
    assert "Mill Dam" not in name_at(path, 42.3119, -71.226175)
    # But standing at its foot, it is worth having.
    assert name_at(path, 42.3165, -71.2262).startswith("Mill Dam")


def test_joining_a_city_uses_the_things_own_radius(tmp_path):
    """750 m from a theatre is not at the theatre, even though 750 m from an
    airport is inside one."""
    path = _world(
        tmp_path,
        [("Bigcity", "US", "MA", 42.3119, -71.2262, 400_000)],
        [("Grand Theatre", "US", "THTR", 42.3164, -71.2262)],       # ~500 m
    )
    got = name_at(path, 42.3119, -71.226175)
    assert "Grand Theatre" not in got, got
    assert got.startswith("Bigcity")


def test_a_demolished_park_does_not_caption_the_airport_built_over_it(tmp_path):
    """GeoNames keeps features that no longer exist, marked "(historical)" --
    123,855 of them in the United States alone. "Wood Island Park (historical)"
    was demolished to build Boston's airport, and it captioned a photo taken
    inside that airport's terminal.

    Geometry from the real dump: the defunct park 252 m away, a beach at 314 m,
    and Logan International Airport 2.03 km away.
    """
    path = _world(
        tmp_path,
        [("Orient Heights", "US", "MA", 42.3922, -71.0143, 15_741)],
        [("Wood Island Park (historical)", "US", "PRK", 42.3855, -71.0143),
         ("Orient Heights Beach", "US", "BCH", 42.3860, -71.0143),
         ("Logan International Airport", "US", "AIRP", 42.36514, -71.01777)],
    )
    got = name_at(path, 42.3832472, -71.0143028)
    assert "historical" not in got, got
    assert "Beach" not in got, got
    assert got == "Logan International Airport, Massachusetts, United States", got


def test_an_airport_outranks_a_neighbourhood_of_fifteen_thousand(tmp_path):
    """Unlike other landmarks, which join a town people have heard of rather
    than replacing it: a tourist inside an airport is in the airport."""
    path = _world(
        tmp_path,
        [("Orient Heights", "US", "MA", 42.3922, -71.0143, 15_741)],
        [("Logan International Airport", "US", "AIRP", 42.36514, -71.01777)],
    )
    got = name_at(path, 42.3832472, -71.0143028)
    assert got.startswith("Logan International Airport"), got
    assert "Orient Heights" not in got


def test_an_amusement_park_does_not_displace_a_city(tmp_path):
    """The airport rule must not generalise to every large attraction: making
    all of them override the town turned central Tel Aviv into "Tel Aviv Luna
    Park" and Times Square into an amusement park."""
    path = _world(
        tmp_path,
        [("Bigcity", "US", "MA", 42.3119, -71.2262, 400_000)],
        [("Fun Land", "US", "AMUS", 42.3299, -71.2262),          # ~2 km
         ("City Gardens", "US", "PRK", 42.3141, -71.2262)],      # ~245 m
    )
    got = name_at(path, 42.3119, -71.226175)
    assert "Fun Land" not in got, got
    assert got == "City Gardens, Bigcity, Massachusetts, United States", got


def test_the_state_is_kept_even_with_a_landmark_and_a_town(tmp_path):
    """"Orient Heights, United States" is a poor answer when Massachusetts is
    the thing that places it."""
    path = _world(
        tmp_path,
        [("Sometown", "US", "MA", 42.3119, -71.2262, 400_000)],
        [("A Monument", "US", "MNMT", 42.3120, -71.2262)],
    )
    got = name_at(path, 42.3119, -71.226175)
    assert got == "A Monument, Sometown, Massachusetts, United States", got


def test_a_small_fairground_does_not_reach_across_a_city(tmp_path):
    """AMUS covers Walt Disney World, a hundred square kilometres, and Tel
    Aviv's Luna Park, a hundred metres across. Being in the fairground is worth
    saying; being two kilometres away is not."""
    path = _world(
        tmp_path,
        [("Bigcity", "US", "MA", 42.3119, -71.2262, 432_000)],
        [("Luna Park", "US", "AMUS", 42.3299, -71.2262)],        # ~2 km
    )
    assert "Luna Park" not in name_at(path, 42.3119, -71.226175)
    # Standing in it, though, it wins over the city.
    got = name_at(path, 42.3298, -71.2262)
    assert got.startswith("Luna Park"), got


def test_a_huge_resort_keeps_its_reach_because_its_neighbour_is_tiny(tmp_path):
    """The same code, the opposite answer, and the neighborhood is what tells
    them apart: Disney's nearest town is a company town of fifty people."""
    path = _world(
        tmp_path,
        [("Bay Lake", "US", "FL", 28.3891, -81.5639, 50)],
        [("Walt Disney World Resort", "US", "AMUS", 28.4034, -81.5639)],
    )
    got = name_at(path, 28.3852, -81.5639)
    assert got.startswith("Walt Disney World Resort"), got


def test_a_reserve_miles_off_does_not_displace_the_suburb_you_are_in(tmp_path):
    """A nature reserve 4.6 km from central Haifa is "within 5 km" of it and
    still not where the photograph was taken."""
    path = _world(
        tmp_path,
        [("Small Suburb", "US", "MA", 42.3156, -71.2262, 0)],     # ~410 m
        [("Far Reserve", "US", "RESN", 42.3532, -71.2262)],       # ~4.6 km
    )
    got = name_at(path, 42.3119, -71.226175)
    assert "Far Reserve" not in got, got
    assert got.startswith("Small Suburb")


def test_a_pond_across_the_neighbourhood_is_not_where_you_are(tmp_path):
    """A photo taken in a shop was captioned "Hammond Pond" -- a pond, 561 m
    off, code LK, which had a three-kilometre radius.

    A lake's recorded point is its centroid: for a small pond that is the pond,
    and for a great lake it is open water no photograph is taken from. So a
    wide radius buys nothing here and costs shop photos their name.
    """
    path = _world(
        tmp_path,
        [("Faraway", "US", "MA", 42.4000, -71.1762, 900)],
        [("Hammond Pond", "US", "LK", 42.3263, -71.1762)],       # 561 m
    )
    assert "Hammond Pond" not in name_at(path, 42.3213, -71.1762278)
    # Standing at the water, it is still the right answer.
    assert name_at(path, 42.3260, -71.1762).startswith("Hammond Pond")


def test_a_shopping_centre_is_worth_naming_from_inside_it(tmp_path):
    """The right answer was sitting in the data all along: the mall was 54 m
    from that shop photo and MALL was not in the allowlist at all."""
    path = _world(
        tmp_path,
        [("Faraway", "US", "MA", 42.4000, -71.1762, 900)],
        [("The Mall at Chestnut Hill", "US", "MALL", 42.3218, -71.1762)],
    )
    got = name_at(path, 42.3213, -71.1762278)                    # 54 m
    assert got.startswith("The Mall at Chestnut Hill"), got
    # From half a kilometre away it is somebody else's afternoon.
    assert "Mall" not in name_at(path, 42.3263, -71.1762)


def test_echo_bridge_from_the_bridge_itself(tmp_path):
    """The other half of the bridge case. 150 m has to exclude the house at
    297 m and still include a photo taken at the bridge."""
    path = _world(
        tmp_path,
        [("Newton Upper Falls", "US", "MA", 42.3173, -71.2262, 9000)],
        [("Echo Bridge", "US", "BDG", 42.3140, -71.2262)],
    )
    assert name_at(path, 42.3139, -71.2262).startswith("Echo Bridge")
    assert "Echo Bridge" not in name_at(path, 42.3119, -71.226175)


def test_a_table_built_with_an_older_code_list_says_so(tmp_path):
    """The table is filtered when it is built, so a feature code added later
    was never stored -- and re-running `geocode` cannot conjure rows the build
    discarded. That bit once: MALL was added to the allowlist and a photo taken
    inside a shopping center went on naming a pond."""
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES ('Town','US','MA',42.0,-71.0,900)")
    conn.execute("INSERT INTO meta VALUES ('landmark_codes','deadbeef')")
    conn.commit(); conn.close()

    gc = geonames.Geocoder(path)
    try:
        assert gc.landmarks_stale is True
    finally:
        gc.close()

    # And current, once it carries this version's fingerprint.
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'landmark_codes'",
                 (geonames.codes_fingerprint(),))
    conn.commit(); conn.close()
    gc = geonames.Geocoder(path)
    try:
        assert gc.landmarks_stale is False
    finally:
        gc.close()


def test_no_landmark_table_is_not_stale(tmp_path):
    """Never built is not the same as out of date; `--landmarks` is opt-in."""
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES ('Town','US','MA',42.0,-71.0,900)")
    conn.commit(); conn.close()
    gc = geonames.Geocoder(path)
    try:
        assert gc.has_landmarks is False
        assert gc.landmarks_stale is False
    finally:
        gc.close()


def test_broadcast_masts_are_not_landmarks(db):
    """16,579 rows in the United States, almost all of them masts, and seven of
    them stood 521 m from one of the photos that prompted this work. Nothing
    famous is lost: the Eiffel Tower is filed as MNMT, and TOWR in France is
    235 old stone towers."""
    assert "TOWR" not in geonames.LANDMARK_CODES
    # The things a traveller does recognize are still there.
    for wanted in ("MNMT", "CSTL", "LTHSE", "DAM", "MALL", "AIRP"):
        assert wanted in geonames.LANDMARK_CODES, wanted


def test_the_download_cache_is_reported_and_removable(tmp_path):
    """421 MB is worth keeping while you are still deciding which landmarks
    you want, and worth reclaiming afterwards."""
    db = tmp_path / "state" / "geonames.sqlite"
    cache = geonames.cache_path(db)
    assert cache == tmp_path / "state" / "geonames-cache"
    assert geonames.cache_size(db) == 0          # nothing downloaded yet

    cache.mkdir(parents=True)
    (cache / "allCountries.zip").write_bytes(b"x" * 5000)
    assert geonames.cache_size(db) == 5000


# ------------------------------------------------ a city, not its neighborhood

def test_a_city_is_named_rather_than_one_of_its_neighbourhoods(tmp_path):
    """A photo in Boston read "Christopher Columbus Park, North End".

    Disqualified for *being a neighborhood*: GeoNames files it as a "section
    of a populated place", so a real city within another kilometre and a half
    is preferred, even though the North End is 288 m nearer and has 10,131
    inhabitants. Boston also happens to have 65x its population, which is a
    second and independent reason, but the section is the first.

    Distances and populations from the real US dump.
    """
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?)", [
        ("Quincy Market", "US", "MA", 42.3608, -71.0507, 0, "PPLX"),      # 202 m
        ("North End", "US", "MA", 42.3627, -71.0507, 10_131, "PPLX"),     # 413 m
        ("Downtown/Financial District", "US", "MA", 42.3630, -71.0507, 0, "PPL"),
        ("Boston", "US", "MA", 42.3653, -71.0507, 653_833, "PPLA"),       # 701 m
    ])
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.MA','Massachusetts')")
    conn.commit(); conn.close()

    got = name_at(path, 42.3589889, -71.0506944)
    assert got == "Boston, Massachusetts, United States", got


def test_an_older_dataset_without_codes_still_works(tmp_path):
    """A geonames.sqlite built before this simply behaves as it did."""
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE places (name TEXT, cc TEXT, admin1 TEXT, lat REAL, "
        "lon REAL, pop INTEGER);"
        "CREATE TABLE countries (cc TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE admin1 (key TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES ('Oldtown','US','MA',42.36,-71.05,900)")
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.MA','Massachusetts')")
    conn.commit(); conn.close()

    gc = geonames.Geocoder(path)
    try:
        assert gc._has_place_codes is False
        assert gc.describe(42.3589889, -71.0506944)[0].startswith("Oldtown")
    finally:
        gc.close()


def test_rebuilding_places_keeps_the_landmarks(tmp_path):
    """`init --geonames` writes a new file and renames it over the old one,
    which would take a 421 MB download's worth of landmarks with it."""
    old = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(old)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)",
                     [(f"L{i}", "US", "PRK", 42.0 + i / 1000, -71.0)
                      for i in range(50)])
    conn.execute("INSERT INTO meta VALUES ('landmark_codes','abc123')")
    conn.commit(); conn.close()

    new = tmp_path / "new.sqlite"
    c = sqlite3.connect(new); c.executescript(geonames.SCHEMA); c.commit(); c.close()
    assert geonames._carry_over_landmarks(old, new) == 50

    c = sqlite3.connect(new)
    assert c.execute("SELECT count(*) FROM landmarks").fetchone()[0] == 50
    assert c.execute(
        "SELECT value FROM meta WHERE key='landmark_codes'").fetchone()[0] == "abc123"
    c.close()


def _places(tmp_path, rows):
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.MA','Massachusetts')")
    conn.commit(); conn.close()
    return path


def test_a_hospital_filed_as_a_town_loses_to_the_city(tmp_path):
    """cities500 is not only towns. "VA Boston Healthcare System, Brockton
    Campus" is recorded as a populated place of 5,474 and GeoNames puts it in
    downtown Boston, though Brockton is thirty kilometres south -- so a photo
    by the Charles was labelled with a hospital in the wrong city.

    Boston has 119x its population and the photo is well inside Boston.
    """
    path = _places(tmp_path, [
        ("VA Boston Healthcare System, Brockton Campus",
         "US", "MA", 42.3664, -71.0634, 5474, "PPL"),          # 297 m
        ("Boston", "US", "MA", 42.3692, -71.0634, 653_833, "PPLA"),   # 610 m
    ])
    got = name_at(path, 42.3637556, -71.0634139)
    assert "Healthcare" not in got, got
    assert got == "Boston, Massachusetts, United States", got


def test_a_village_keeps_its_name_against_its_own_town(tmp_path):
    """Newton has 11.7x the population of Newton Upper Falls, which is a
    village with a name of its own and the right answer for a photo in it.
    Boston beats a misplaced hospital record by 119x. The threshold has to sit
    between those, which is why it is twenty rather than ten."""
    path = _places(tmp_path, [
        ("Newton Upper Falls", "US", "MA", 42.3173, -71.2262, 7579, "PPL"),
        ("Newton Highlands", "US", "MA", 42.3336, -71.2262, 9976, "PPL"),
        ("Newton", "US", "MA", 42.3400, -71.2262, 88_817, "PPLA"),
    ])
    got = name_at(path, 42.3119, -71.226175)
    assert got.startswith("Newton Upper Falls"), got


def test_a_distant_metropolis_does_not_reach_out_to_a_village(tmp_path):
    """Dominance is not enough on its own: a place also has to be near enough
    that you are plausibly inside it, which is estimated from its population.

    This is what stops a village being swallowed. An earlier version of this
    test put the metropolis 1.2 km from the village and expected the village to
    win, which was a bad premise -- 1.2 km from the middle of a city of 653,833
    means you are in that city, and the "village" is one of its neighborhoods.
    """
    path = _places(tmp_path, [
        ("Tiny Village", "US", "MA", 42.3119, -71.2262, 400, "PPL"),
        # 653,833 people extend about 10 km; this is 30 km away.
        ("Far Metropolis", "US", "MA", 42.5814, -71.2262, 653_833, "PPLA"),
    ])
    got = name_at(path, 42.3119, -71.226175)
    assert got.startswith("Tiny Village"), got


def test_naming_the_city_does_not_make_the_surroundings_look_rural(tmp_path):
    """The area-shrink asks how built-up the spot is, which is not the same
    question as what to call it. Keying it off the chosen name brought the
    historic district back: Newton's center is 3 km away, so the surroundings
    looked like open country while the photo was in a suburb."""
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executescript(geonames.LANDMARK_SCHEMA)
    conn.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?)", [
        ("Newton Upper Falls", "US", "MA", 42.3173, -71.2262, 7579, "PPL"),
        ("Newton", "US", "MA", 42.3400, -71.2262, 88_817, "PPLA"),
    ])
    conn.executemany("INSERT INTO landmarks VALUES (?,?,?,?,?)", [
        ("Newton Upper Falls Historic District", "US", "PRK", 42.3157, -71.2262),
    ])
    conn.execute("INSERT INTO countries VALUES ('US','United States')")
    conn.execute("INSERT INTO admin1 VALUES ('US.MA','Massachusetts')")
    conn.commit(); conn.close()

    got = name_at(path, 42.3119, -71.226175)
    assert "Historic District" not in got, got
