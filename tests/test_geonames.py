"""Offline reverse geocoding (DESIGN.md 9.5).

The dataset itself is a 14 MB download, so these tests build a tiny stand-in
with the same schema rather than requiring the network.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sqlite3

import pytest

from harelphotos import geonames


@pytest.fixture
def gc(tmp_path):
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executemany(
        "INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES (?,?,?,?,?,?)",
        [
            ("Náxos", "GR", "83", 37.1036, 25.3766, 7000),
            ("Filoti", "GR", "83", 37.0472, 25.5236, 1500),
            ("Tel Aviv", "IL", "05", 32.0853, 34.7818, 400000),
            ("Bay Lake", "US", "FL", 28.3852, -81.5639, 47),
        ],
    )
    conn.executemany(
        "INSERT INTO countries VALUES (?,?)",
        [("GR", "Greece"), ("IL", "Israel"), ("US", "United States")],
    )
    conn.executemany(
        "INSERT INTO admin1 VALUES (?,?)",
        [("GR.83", "South Aegean"), ("IL.05", "Tel Aviv"), ("US.FL", "Florida")],
    )
    conn.commit()
    conn.close()
    g = geonames.Geocoder(path)
    yield g
    g.close()


def test_nearest_place(gc):
    place, dist = gc.describe(37.1036, 25.3766)
    # No "South Aegean": a first-level subdivision is printed only where a
    # traveller would use it, which is a state in a large federal country, not
    # a region or district the country name has already placed.
    assert place == "Náxos, Greece"
    assert dist < 100


def test_picks_the_nearer_of_two(gc):
    place, _ = gc.describe(37.05, 25.52)
    assert place.startswith("Filoti")


def test_region_is_omitted_when_it_repeats_the_city(gc):
    # "Tel Aviv, Tel Aviv, Israel" reads badly.
    place, _ = gc.describe(32.0853, 34.7818)
    assert place == "Tel Aviv, Israel"


def test_far_away_places_are_hedged(gc):
    place, dist = gc.describe(37.30, 25.60)      # ~30 km from anywhere here
    assert dist > geonames.NEAR_THRESHOLD_M
    assert geonames.format_place(place, dist).startswith("near ")
    assert "km)" in geonames.format_place(place, dist)


def test_close_places_are_not_hedged(gc):
    place, dist = gc.describe(37.1036, 25.3766)
    assert geonames.format_place(place, dist) == place


def test_middle_of_the_pacific_resolves_to_nothing(gc):
    assert gc.describe(-40.0, -140.0) is None


def test_results_are_memoised_at_about_100m(gc):
    gc.describe(37.1036, 25.3766)
    gc.describe(37.10361, 25.37661)      # same to 3 decimals
    assert len(gc._cache) == 1
    gc.describe(37.2000, 25.3766)
    assert len(gc._cache) == 2


def test_missing_dataset_says_what_to_run(tmp_path):
    with pytest.raises(geonames.GeonamesError, match="init --geonames"):
        geonames.Geocoder(tmp_path / "absent.sqlite")


def test_haversine_is_sane():
    # Tel Aviv to Jerusalem is about 54 km.
    d = geonames.haversine(32.0853, 34.7818, 31.7683, 35.2137)
    assert 50_000 < d < 60_000
    assert geonames.haversine(0, 0, 0, 0) == 0


def test_geocode_reports_progress_on_every_row(tmp_path, monkeypatch):
    """It ran silently. Reported per row so the caller can draw on a clock:
    a count-based interval shows nothing on a small collection and appears to
    stall on a slow one -- the same mistake the scanner already made once."""
    import json as _json
    import sqlite3 as _sqlite3

    from harelphotos import db as db_mod, geocode as geocode_mod, scanner
    from tests import fixtures

    photos = tmp_path / "pictures"
    for i in range(5):
        fixtures.make_jpeg(photos / f"p{i}.jpg", gps=(37.1036, 25.3766))
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)

    # A minimal dataset beside the index, where geocode expects it.
    gpath = cfg.state_dir / "geonames.sqlite"
    g = _sqlite3.connect(gpath)
    g.executescript(geonames.SCHEMA)
    g.execute("INSERT INTO places (name, cc, admin1, lat, lon, pop) VALUES ('Náxos','GR','24',37.1036,25.3766,7000)")
    g.execute("INSERT INTO countries VALUES ('GR','Greece')")
    g.commit(); g.close()

    seen = []
    stats = geocode_mod.geocode(cfg, conn, progress=lambda d, t: seen.append((d, t)))
    conn.close()

    assert stats.resolved == 5
    # One per row, not one per 500 -- plus a final call so the completed line
    # is always drawn even when the last row did not fall on a tick.
    assert [d for d, _ in seen] == [1, 2, 3, 4, 5, 5], seen
    assert all(t == 5 for _, t in seen)
    assert stats.elapsed >= 0


def test_place_row_keeps_villages_and_drops_the_departed():
    """What `--villages` lets into the places table.

    Everything in GeoNames' P class is somewhere people live, including the
    codes for a section of a town -- those belong in the table, because
    `_is_vague` and the city rules decide whether to *say* them and cannot
    decide about a row that is not there.

    Historical, abandoned and destroyed are the exception, for the same reason
    "(historical)" landmarks are dropped: naming a photograph after a village
    that is gone is worse than naming nothing.
    """
    def row(code, pop="0", cls="P"):
        f = [""] * 19
        f[geonames.COL_NAME] = "Somewhere"
        f[geonames.COL_LAT], f[geonames.COL_LON] = "47.8", "7.9"
        f[geonames.COL_CC], f[geonames.COL_ADMIN1] = "DE", "01"
        f[geonames.COL_FCLASS], f[geonames.COL_FCODE] = cls, code
        f[geonames.COL_POP] = pop
        return geonames.place_row(f)

    assert row("PPL") is not None                 # a village with no population
    assert row("PPL", "5072") is not None
    assert row("PPLA4") is not None               # a municipal subdivision
    assert row("PPLX") is not None                # a section: kept, judged later
    for gone in ("PPLH", "PPLQ", "PPLW"):
        assert row(gone) is None, gone
    assert row("MT", cls="T") is None             # not a populated place at all
    assert geonames.place_row(["short", "row"]) is None


def test_a_village_with_no_population_is_a_place(tmp_path):
    """The Black Forest case, in miniature.

    Three photographs taken in one apartment came back as a hill, a forest and
    a town, because Muggenbrunn -- 160 m away, population unrecorded -- was not
    in `cities500` and the rules were choosing between things three kilometres
    off. Nine metres of GPS drift decided which of two distant towns was
    nearest, and those two fell on opposite sides of the population floor.
    """
    path = tmp_path / "geonames.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(geonames.SCHEMA)
    conn.executemany(
        "INSERT INTO places (name, cc, admin1, lat, lon, pop, code) VALUES (?,?,?,?,?,?,?)",
        [("Muggenbrunn", "DE", "01", 47.8558, 7.9182, 0, "PPL"),     # ~160 m
         ("Todtnau", "DE", "01", 47.8290, 7.9410, 5072, "PPL"),      # ~3.3 km
         ("Wieden", "DE", "01", 47.8430, 7.8830, 578, "PPL")],       # ~3.1 km
    )
    conn.execute("INSERT INTO countries VALUES ('DE','Germany')")
    conn.commit(); conn.close()

    gc = geonames.Geocoder(path)
    try:
        for lat, lon in ((47.857021, 7.9163261), (47.8548144, 7.9212054),
                         (47.8547626, 7.9219531)):
            got = gc.describe(lat, lon)
            assert got and got[0].startswith("Muggenbrunn"), (lat, lon, got)
    finally:
        gc.close()
