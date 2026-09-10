"""users.toml (DESIGN.md 5.2)."""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from harelphotos import users as users_mod


def test_absent_file_is_an_empty_allowlist(tmp_path):
    # The state right after 'init': not an error, just nobody invited yet.
    us = users_mod.load(tmp_path / "users.toml")
    assert len(us) == 0


def test_parse_local_and_google_accounts():
    us = users_mod.parse(
        """
        [users.nyh]
        name     = "Nadav"
        password = "scrypt:fake"
        google   = "someone@gmail.com"
        admin    = true

        [users."dad@gmail.com"]
        name   = "Dad"
        google = "dad@gmail.com"
        """
    )
    assert len(us) == 2
    nyh = us.get("nyh")
    assert nyh.name == "Nadav" and nyh.admin and nyh.can_login_locally
    dad = us.get("dad@gmail.com")
    assert dad.name == "Dad" and not dad.can_login_locally
    assert nyh.epoch == 1


def test_account_with_no_login_method_is_rejected():
    # Silently keeping an account nobody can ever use is worse than complaining.
    with pytest.raises(users_mod.UsersError, match="never be used"):
        users_mod.parse('[users.ghost]\nname = "Ghost"\n')


def test_invalid_toml():
    with pytest.raises(users_mod.UsersError, match="invalid TOML"):
        users_mod.parse("[users.a\n")


def test_google_lookup_by_explicit_field():
    us = users_mod.parse('[users.nyh]\npassword = "x"\ngoogle = "n@gmail.com"\n')
    assert us.by_google_email("n@gmail.com").token == "nyh"
    assert us.by_google_email("N@Gmail.COM").token == "nyh"      # case-insensitive
    assert us.by_google_email(" n@gmail.com ").token == "nyh"    # whitespace tolerant


def test_google_lookup_falls_back_to_the_table_key():
    us = users_mod.parse('[users."dad@gmail.com"]\ngoogle = "dad@gmail.com"\n')
    assert us.by_google_email("dad@gmail.com").token == "dad@gmail.com"


def test_unknown_google_email_is_refused():
    us = users_mod.parse('[users.nyh]\npassword = "x"\n')
    assert us.by_google_email("stranger@gmail.com") is None


def test_password_hash_round_trip():
    h = users_mod.hash_password("correct horse")
    u = users_mod.User(token="a", name="A", password_hash=h)
    assert u.check_password("correct horse")
    assert not u.check_password("wrong")


def test_google_only_account_rejects_every_password():
    u = users_mod.User(token="a", name="A", google="a@gmail.com")
    assert not u.check_password("")
    assert not u.check_password("anything")


def test_save_and_reload_round_trip(tmp_path):
    path = tmp_path / "users.toml"
    original = users_mod.Users(
        by_token={
            "nyh": users_mod.User(
                token="nyh",
                name="Nadav",
                password_hash=users_mod.hash_password("pw"),
                google="n@gmail.com",
                admin=True,
                epoch=3,
            ),
            "dad@gmail.com": users_mod.User(
                token="dad@gmail.com", name="Dad", google="dad@gmail.com"
            ),
        }
    )
    users_mod.save(path, original)
    back = users_mod.load(path)
    assert len(back) == 2
    assert back.get("nyh").epoch == 3
    assert back.get("nyh").admin
    assert back.get("nyh").check_password("pw")
    # An email key needs quoting to be valid TOML; assert it survives.
    assert back.get("dad@gmail.com").name == "Dad"


def test_saved_file_is_not_world_readable(tmp_path):
    # It holds password hashes.
    path = tmp_path / "users.toml"
    users_mod.save(path, users_mod.Users(by_token={}))
    assert path.stat().st_mode & 0o077 == 0


def test_dummy_check_returns_false_and_does_work():
    # Used so an unknown username costs the same time as a known one.
    assert users_mod.waste_time_like_a_real_check("anything") is False
