"""Global config loading (DESIGN.md 5.1).

Unlike .album.toml, a bad global config is fatal — we would not know where the
photos are — so these assert that problems raise with a useful message.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path

import pytest

from harelphotos import config as config_mod

MINIMAL = """
photo_root      = "/photos"
derived_root    = "/state/derived"
index_db        = "/state/index.sqlite"
users_file      = "/etc/harelphotos/users.toml"
secret_key_file = "/etc/harelphotos/secret_key"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_minimal_config_and_defaults(tmp_path):
    cfg = config_mod.load(write(tmp_path, MINIMAL))
    assert cfg.photo_root == Path("/photos")
    assert cfg.sizes.thumb == (256, 512)
    assert cfg.sizes.view == (1280, 1600)
    # Tiers are descending: the cascade resizes each from the one above.
    assert cfg.sizes.tiers == (1600, 1280, 512, 256)
    assert cfg.encode.format == "avif"
    assert cfg.encode.fallback == "auto"
    assert cfg.encode.speed == 6
    assert cfg.ui.map_link == "osm"
    assert cfg.sendfile_header == "auto"
    assert cfg.google.enabled is False
    assert cfg.state_dir == Path("/state")


def test_missing_required_key(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="photo_root"):
        config_mod.load(write(tmp_path, 'derived_root = "/d"'))


def test_invalid_toml(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="invalid TOML"):
        config_mod.load(write(tmp_path, 'photo_root = "unclosed'))


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="not found"):
        config_mod.load(tmp_path / "nope.toml")


def test_env_var_is_honoured(tmp_path, monkeypatch):
    p = write(tmp_path, MINIMAL)
    monkeypatch.setenv(config_mod.CONFIG_ENV, str(p))
    assert config_mod.load().source == p


def test_env_var_pointing_at_nothing_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv(config_mod.CONFIG_ENV, str(tmp_path / "nope.toml"))
    with pytest.raises(config_mod.ConfigError, match=config_mod.CONFIG_ENV):
        config_mod.load()


def test_search_order_finds_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv(config_mod.CONFIG_ENV, raising=False)
    write(tmp_path, MINIMAL)
    monkeypatch.chdir(tmp_path)
    assert config_mod.load().source == Path("config.toml")


def test_no_config_anywhere_suggests_init(tmp_path, monkeypatch):
    monkeypatch.delenv(config_mod.CONFIG_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_SEARCH", (Path("config.toml"),))
    with pytest.raises(config_mod.ConfigError, match="harelphotos init"):
        config_mod.load()


def test_tilde_is_expanded(tmp_path):
    cfg = config_mod.load(write(tmp_path, MINIMAL.replace('"/photos"', '"~/pictures"')))
    assert cfg.photo_root == Path.home() / "pictures"
    assert "~" not in str(cfg.photo_root)


def test_custom_size_ladder(tmp_path):
    cfg = config_mod.load(
        write(tmp_path, MINIMAL + "\n[sizes]\nthumb = [400]\nview = [1600]\n")
    )
    assert cfg.sizes.thumb == (400,)
    assert cfg.sizes.tiers == (1600, 400)


def test_bare_integer_tier_accepted(tmp_path):
    cfg = config_mod.load(write(tmp_path, MINIMAL + "\n[sizes]\nthumb = 400\n"))
    assert cfg.sizes.thumb == (400,)


def test_bad_tier_values_rejected(tmp_path):
    for bad in ("[]", '["big"]', "[0]", "[-5]"):
        with pytest.raises(config_mod.ConfigError, match="sizes"):
            config_mod.load(write(tmp_path, MINIMAL + f"\n[sizes]\nthumb = {bad}\n"))


def test_quality_keys_may_be_integers_or_strings(tmp_path):
    cfg = config_mod.load(
        write(tmp_path, MINIMAL + "\n[encode]\nquality = { 256 = 60, 512 = 55 }\n")
    )
    assert cfg.encode.quality_for(256) == 60
    assert cfg.encode.quality_for(512) == 55
    # An unlisted tier falls back to quality_default rather than crashing.
    assert cfg.encode.quality_for(9999) == 46


def test_speed_range_enforced(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="speed"):
        config_mod.load(write(tmp_path, MINIMAL + "\n[encode]\nspeed = 42\n"))


def test_enum_values_rejected_with_the_allowed_list(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="map_link"):
        config_mod.load(write(tmp_path, MINIMAL + '\n[ui]\nmap_link = "bing"\n'))
    with pytest.raises(config_mod.ConfigError, match="fallback"):
        config_mod.load(write(tmp_path, MINIMAL + '\n[encode]\nfallback = "maybe"\n'))


def test_groups_parsed(tmp_path):
    cfg = config_mod.load(
        write(tmp_path, MINIMAL + '\n[groups]\nfamily = ["a", "b"]\nc = ["@family"]\n')
    )
    assert cfg.groups["family"] == ("a", "b")
    assert cfg.groups["c"] == ("@family",)


def test_groups_must_be_string_lists(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="groups"):
        config_mod.load(write(tmp_path, MINIMAL + "\n[groups]\nfamily = 5\n"))


def test_google_enabled_requires_credentials(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="client_id"):
        config_mod.load(write(tmp_path, MINIMAL + "\n[google]\nenabled = true\n"))


def test_google_secret_from_file(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("  s3cret\n", encoding="utf-8")
    cfg = config_mod.load(
        write(
            tmp_path,
            MINIMAL
            + f'\n[google]\nenabled = true\nclient_id = "cid"\n'
            f'client_secret_file = "{secret}"\n',
        )
    )
    assert cfg.google.client_secret == "s3cret"


def test_jobs_zero_means_all_cores(tmp_path):
    cfg = config_mod.load(write(tmp_path, MINIMAL))
    assert cfg.scan.jobs == 0
    assert cfg.scan.effective_jobs == (os.cpu_count() or 1)


def test_base_url_trailing_slash_stripped(tmp_path):
    cfg = config_mod.load(write(tmp_path, MINIMAL + '\nbase_url = "https://h.example/"\n'))
    assert cfg.base_url == "https://h.example"


def test_session_days_defaults_and_overrides(tmp_path):
    assert config_mod.load(write(tmp_path, MINIMAL)).session_days == 30
    cfg = config_mod.load(write(tmp_path, MINIMAL + "\nsession_days = 7\n"))
    assert cfg.session_days == 7


def test_session_days_must_be_a_positive_integer(tmp_path):
    # `true` is a mistake, not a 1 -- bool being an int subclass would let it
    # silently mean "one day".
    for bad in ("0", "-1", '"forever"', "true", "1.5"):
        with pytest.raises(config_mod.ConfigError, match="session_days"):
            config_mod.load(write(tmp_path, MINIMAL + f"\nsession_days = {bad}\n"))
