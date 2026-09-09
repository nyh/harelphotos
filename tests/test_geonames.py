"""Offline reverse geocoding (DESIGN.md 9.5).

The dataset itself is a 14 MB download, so these tests build a tiny stand-in
with the same schema rather than requiring the network.
"""

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
        "INSERT INTO places VALUES (?,?,?,?,?,?)",
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
    assert place == "Náxos, South Aegean, Greece"
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
