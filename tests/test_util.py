# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

from harelphotos.util import natkey, prettify_name


def sorted_by_natkey(names):
    return sorted(names, key=natkey)


def test_digit_runs_compare_numerically():
    # The whole point: plain lexicographic sorting gets these wrong, and they
    # are exactly the naming patterns people use (DESIGN.md 11.1b).
    assert sorted_by_natkey(["Day 10", "Day 2", "Day 1"]) == ["Day 1", "Day 2", "Day 10"]
    assert sorted_by_natkey(["2010", "2009", "1998"]) == ["1998", "2009", "2010"]
    assert sorted_by_natkey(["img12", "img2", "img100"]) == ["img2", "img12", "img100"]


def test_case_insensitive():
    assert sorted_by_natkey(["beta", "Alpha"]) == ["Alpha", "beta"]


def test_multiple_digit_runs():
    assert sorted_by_natkey(["v1-9", "v1-10", "v2-1"]) == ["v1-9", "v1-10", "v2-1"]


def test_large_numbers_do_not_overflow_the_pad():
    # Ten digits of padding; make sure something long still orders sanely.
    assert natkey("x999999999") < natkey("x1000000000")


def test_non_digits_unaffected():
    assert sorted_by_natkey(["ancient", "summer", "Passover"]) == [
        "ancient",
        "Passover",
        "summer",
    ]


def test_prettify_name():
    assert prettify_name("summer_trip") == "summer trip"
    assert prettify_name("2019") == "2019"
    assert prettify_name("a__b") == "a b"
    assert prettify_name("") == ""
