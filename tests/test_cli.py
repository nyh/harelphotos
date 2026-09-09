"""End-to-end CLI behaviour for the M1 commands."""

from pathlib import Path

import pytest

from harelphotos import cli, config as config_mod, db, users as users_mod


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A freshly initialised project, with $HARELPHOTOS_CONFIG pointing at it."""
    photos = tmp_path / "pictures"
    photos.mkdir()
    cfgpath = tmp_path / "etc" / "config.toml"
    assert cli.main(
        [
            "-c", str(cfgpath),
            "init",
            "--photo-root", str(photos),
            "--state-dir", str(tmp_path / "state"),
        ]
    ) == 0
    monkeypatch.setenv(config_mod.CONFIG_ENV, str(cfgpath))
    return cfgpath


def test_init_creates_everything(project, tmp_path):
    cfg = config_mod.load(project)
    assert cfg.photo_root == (tmp_path / "pictures")
    assert cfg.secret_key_file.exists()
    assert cfg.index_db.exists()
    assert cfg.derived_root.is_dir()
    assert (project.parent / "privacy.md").exists()
    assert (project.parent / "terms.md").exists()
    conn = db.open_index(cfg.index_db, read_only=True)
    conn.close()


def test_secret_key_is_32_bytes_and_private(project):
    cfg = config_mod.load(project)
    assert len(cfg.secret_key_file.read_bytes()) == 32
    assert cfg.secret_key_file.stat().st_mode & 0o077 == 0


def test_init_is_idempotent_and_never_rotates_the_key(project):
    cfg = config_mod.load(project)
    before = cfg.secret_key_file.read_bytes()
    cfg.users_file.write_text('[users.keep]\npassword = "x"\n', encoding="utf-8")
    assert cli.main(
        ["-c", str(project), "init", "--photo-root", str(cfg.photo_root)]
    ) == 0
    # Rotating the key would log the whole family out.
    assert cfg.secret_key_file.read_bytes() == before
    # ...and an existing users.toml must survive too.
    assert users_mod.load(cfg.users_file).get("keep") is not None


def test_init_requires_photo_root(tmp_path, capsys):
    assert cli.main(["-c", str(tmp_path / "c.toml"), "init"]) == 2
    assert "photo-root" in capsys.readouterr().err


def test_init_rejects_a_nonexistent_photo_root(tmp_path, capsys):
    assert cli.main(
        ["-c", str(tmp_path / "c.toml"), "init", "--photo-root", str(tmp_path / "nope")]
    ) == 2
    assert "not a directory" in capsys.readouterr().err


def test_user_add_list_and_revoke(project, capsys):
    assert cli.main(["user", "add", "nyh", "--password", "pw", "--admin"]) == 0
    assert cli.main(["user", "list"]) == 0
    out = capsys.readouterr().out
    assert "nyh" in out

    cfg = config_mod.load(project)
    u = users_mod.load(cfg.users_file).get("nyh")
    assert u.admin and u.check_password("pw") and u.epoch == 1

    assert cli.main(["user", "revoke", "nyh"]) == 0
    assert users_mod.load(cfg.users_file).get("nyh").epoch == 2


def test_user_add_google_only_has_no_password(project):
    assert cli.main(
        ["user", "add", "dad@gmail.com", "--google-only", "--google", "dad@gmail.com"]
    ) == 0
    cfg = config_mod.load(project)
    u = users_mod.load(cfg.users_file).get("dad@gmail.com")
    assert not u.can_login_locally
    assert u.google == "dad@gmail.com"


def test_user_add_refuses_duplicates(project, capsys):
    assert cli.main(["user", "add", "nyh", "--password", "pw"]) == 0
    assert cli.main(["user", "add", "nyh", "--password", "pw"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_user_del(project):
    assert cli.main(["user", "add", "tmp", "--password", "pw"]) == 0
    assert cli.main(["user", "del", "tmp"]) == 0
    cfg = config_mod.load(project)
    assert users_mod.load(cfg.users_file).get("tmp") is None


def test_user_commands_on_unknown_name(project):
    assert cli.main(["user", "del", "ghost"]) == 1
    assert cli.main(["user", "revoke", "ghost"]) == 1


def test_config_show(project, capsys):
    assert cli.main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "1600, 1280, 512, 256" in out
    assert "avif" in out


def test_unimplemented_commands_say_which_milestone(project, capsys):
    assert cli.main(["acl"]) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err and "M5" in err


def test_missing_config_is_a_clean_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(config_mod.CONFIG_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_SEARCH", ())
    assert cli.main(["user", "list"]) == 1
    assert "no config.toml found" in capsys.readouterr().err


def test_serve_refuses_an_empty_index(project, capsys):
    assert cli.main(["serve"]) == 1
    assert "run 'harelphotos scan' first" in capsys.readouterr().err


def test_the_no_listen_guard_is_real(project, tmp_path):
    """conftest's guard is load-bearing, not decorative.

    A test that starts a server blocks forever and orphans a process holding
    the port — which is exactly what happened once, to port 5000.
    """
    import socket

    import pytest

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        with pytest.raises(AssertionError, match="must not start a server"):
            s.listen(1)


def test_check_env_reports_on_this_machine(project, capsys):
    """The only view of a machine I cannot log in to, so it must not crash."""
    assert cli.main(["check", "--env"]) in (0, 1)
    out = capsys.readouterr().out
    for expected in ("python", "Pillow AVIF support", "photo_root", "secret_key"):
        assert expected in out, expected


def test_check_env_fails_when_avif_is_missing(project, monkeypatch, capsys):
    """A Pillow without AVIF otherwise fails at the first encode, hours in."""
    from PIL import features

    monkeypatch.setattr(features, "check", lambda f: False if f == "avif" else True)
    assert cli.main(["check", "--env"]) == 1
    assert "avif=NO" in capsys.readouterr().out


def test_check_env_works_with_no_config_at_all(tmp_path, monkeypatch, capsys):
    """The first thing run on a fresh server, before 'init'.

    Requiring a config here defeated the whole purpose: the machine-level
    checks — python version, Pillow AVIF, Apache modules, SELinux — are
    precisely what you want *before* configuring anything.
    """
    monkeypatch.delenv(config_mod.CONFIG_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_SEARCH", (Path("config.toml"),))
    assert cli.main(["check", "--env"]) in (0, 1)
    out = capsys.readouterr()
    assert "python" in out.out
    assert "Pillow AVIF support" in out.out
    assert "none yet" in out.out          # says what is missing, does not abort
    assert "harelphotos init" in out.err
