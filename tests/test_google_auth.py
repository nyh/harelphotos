"""Sign in with Google (DESIGN.md 12.2).

Every test here is about refusing to sign in the wrong person. The happy path
is one test; the rest are the ways a token can be genuine but not ours.
"""

import base64
import json
import time

import pytest

from harelphotos import google_auth

CLIENT_ID = "cid.apps.googleusercontent.com"


def make_config(tmp_path, **over):
    from harelphotos import config as config_mod

    body = (
        'photo_root = "/photos"\n'
        'derived_root = "/s/d"\n'
        'index_db = "/s/i.sqlite"\n'
        'users_file = "/s/users.toml"\n'
        'secret_key_file = "/s/key"\n'
        'base_url = "https://photos.example.org"\n'
        "[google]\nenabled = true\n"
        f'client_id = "{CLIENT_ID}"\nclient_secret = "shh"\n'
    )
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    cfg = config_mod.load(p)
    for k, v in over.items():
        object.__setattr__(cfg, k, v)
    return cfg


def id_token(**claims) -> str:
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 3600,
        "email": "someone@gmail.com",
        "email_verified": True,
        "sub": "12345",
    }
    payload.update(claims)
    enc = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{enc}.signature"


def claims(**over):
    return google_auth.claims_from_id_token(id_token(**over))


def test_a_good_token_yields_the_email(tmp_path):
    cfg = make_config(tmp_path)
    ident = google_auth.check_claims(cfg, claims(), nonce=None)
    assert ident.email == "someone@gmail.com"
    assert ident.subject == "12345"


def test_a_token_for_another_client_is_refused(tmp_path):
    """A real Google token, just not minted for us. Without the audience
    check it could be replayed here from any other site."""
    cfg = make_config(tmp_path)
    with pytest.raises(google_auth.GoogleAuthError, match="different client"):
        google_auth.check_claims(cfg, claims(aud="someone-elses-client"), nonce=None)


def test_an_expired_token_is_refused(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(google_auth.GoogleAuthError, match="expired"):
        google_auth.check_claims(cfg, claims(exp=time.time() - 3600), nonce=None)


def test_a_token_with_no_expiry_is_refused(tmp_path):
    """Missing must not read as 'never expires'."""
    cfg = make_config(tmp_path)
    c = claims()
    del c["exp"]
    with pytest.raises(google_auth.GoogleAuthError, match="expired"):
        google_auth.check_claims(cfg, c, nonce=None)


def test_a_foreign_issuer_is_refused(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(google_auth.GoogleAuthError, match="issuer"):
        google_auth.check_claims(cfg, claims(iss="https://evil.example"), nonce=None)


def test_an_unverified_email_is_refused(tmp_path):
    """The decisive one.

    An unverified address is one the account holder simply typed in. If it were
    accepted, anyone could create a Google account claiming a family member's
    address and be let straight in.
    """
    cfg = make_config(tmp_path)
    with pytest.raises(google_auth.GoogleAuthError, match="not verified"):
        google_auth.check_claims(cfg, claims(email_verified=False), nonce=None)


def test_a_mismatched_nonce_is_refused(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(google_auth.GoogleAuthError, match="nonce"):
        google_auth.check_claims(cfg, claims(nonce="a"), nonce="b")


def test_malformed_tokens_raise_rather_than_crash(tmp_path):
    for bad in ("", "one.two", "a.!!!not-base64!!!.c", "a." + base64.urlsafe_b64encode(
        b"not json").decode().rstrip("=") + ".c"):
        with pytest.raises(google_auth.GoogleAuthError):
            google_auth.claims_from_id_token(bad)


def test_state_must_match(tmp_path):
    """What stops someone completing their own sign-in in a victim's browser."""
    cfg = make_config(tmp_path)
    session = {}
    google_auth.begin(cfg, session, next_url="/a/2019/")
    with pytest.raises(google_auth.GoogleAuthError, match="state does not match"):
        google_auth.complete(cfg, session, code="x", state="not-the-state")


def test_a_callback_with_no_sign_in_in_progress_is_refused(tmp_path):
    cfg = make_config(tmp_path)
    with pytest.raises(google_auth.GoogleAuthError, match="no sign-in"):
        google_auth.complete(cfg, {}, code="x", state="anything")


def test_state_is_single_use(tmp_path, monkeypatch):
    """Consumed even when the exchange fails, so a code cannot be retried."""
    cfg = make_config(tmp_path)
    session = {}
    url = google_auth.begin(cfg, session, next_url=None)
    state = session[google_auth.SESSION_STATE]
    assert state in url

    monkeypatch.setattr(google_auth, "exchange",
                        lambda *a, **k: (_ for _ in ()).throw(
                            google_auth.GoogleAuthError("nope")))
    with pytest.raises(google_auth.GoogleAuthError):
        google_auth.complete(cfg, session, code="x", state=state)
    assert google_auth.SESSION_STATE not in session
    with pytest.raises(google_auth.GoogleAuthError, match="no sign-in"):
        google_auth.complete(cfg, session, code="x", state=state)


def test_the_authorize_url_asks_for_nothing_but_an_identity(tmp_path):
    """A sensitive scope is what would trigger Google's verification review,
    and we have no use for one."""
    cfg = make_config(tmp_path)
    url = google_auth.begin(cfg, {}, next_url=None)
    assert "scope=openid+email" in url
    assert "response_type=code" in url
    assert "access_type=online" in url
    assert "drive" not in url and "photoslibrary" not in url


def test_redirect_uri_matches_what_is_registered(tmp_path):
    """Google compares this character for character; a trailing slash fails."""
    cfg = make_config(tmp_path)
    assert google_auth.redirect_uri(cfg) == \
        "https://photos.example.org/auth/google/callback"


def test_disabled_unless_fully_configured(tmp_path):
    cfg = make_config(tmp_path)
    assert google_auth.enabled(cfg)
    object.__setattr__(cfg.google, "client_secret", "")
    assert not google_auth.enabled(cfg)
