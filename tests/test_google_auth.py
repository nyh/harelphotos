"""Sign in with Google (DESIGN.md 12.2).

Every test here is about refusing to sign in the wrong person. The happy path
is one test; the rest are the ways a token can be genuine but not ours.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

import base64
import json
import time
from dataclasses import replace

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


# ------------------------------------------------------------ the routes
#
# The module tests above cover the claim checking. These cover the wiring:
# that the feature is genuinely reachable when switched on, genuinely absent
# when not, and that Google authenticating someone is not by itself enough to
# get in.

@pytest.fixture
def google_app(tmp_path):
    from harelphotos import config as config_mod, scanner
    from harelphotos.web import create_app
    from tests import fixtures

    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    fixtures.add_user(cfg, "nyh", "nyh")

    object.__setattr__(cfg, "base_url", "https://photos.example.org")
    object.__setattr__(cfg, "google", config_mod.Google(
        enabled=True, client_id=CLIENT_ID, client_secret="shh"))
    app = create_app(cfg, require_login=True)
    app.config.update(TESTING=True)
    return app, cfg


def test_the_login_page_offers_google_only_when_enabled(google_app, tmp_path):
    from harelphotos.web import create_app
    from tests import fixtures

    app, cfg = google_app
    body = app.test_client().get("/login").get_data(as_text=True)
    assert "Sign in with Google" in body
    assert "/auth/google" in body

    # And with it off, the button and the routes are both gone -- an
    # unconfigured OAuth client must not be advertised.
    object.__setattr__(cfg, "google", type(cfg.google)(enabled=False))
    off = create_app(cfg, require_login=True)
    off.config.update(TESTING=True)
    c = off.test_client()
    assert "Sign in with Google" not in c.get("/login").get_data(as_text=True)
    assert c.get("/auth/google").status_code == 404
    assert c.get("/auth/google/callback").status_code == 404


def test_starting_sign_in_redirects_to_google_with_the_right_parameters(google_app):
    app, cfg = google_app
    r = app.test_client().get("/auth/google?next=/a/2019/01/")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=" + CLIENT_ID.replace(".", "%2E") in loc or CLIENT_ID in loc
    assert "scope=openid+email" in loc
    assert "response_type=code" in loc
    # The redirect_uri must be the https one Google has registered, never the
    # test client's own host.
    assert "photos.example.org%2Fauth%2Fgoogle%2Fcallback" in loc


def test_a_verified_google_user_with_no_account_is_refused(google_app, monkeypatch):
    """The point of the whole design: Google authenticates, users.toml
    authorises. Someone with a perfectly good Google account and no invitation
    must not get in."""
    from harelphotos import google_auth as ga

    app, cfg = google_app
    client = app.test_client()
    client.get("/auth/google")          # establishes the state in the session

    monkeypatch.setattr(ga, "complete",
                        lambda *a, **k: (ga.Identity("stranger@gmail.com", "9"), ""))
    r = client.get("/auth/google/callback?code=x&state=y")
    assert r.status_code == 403
    assert "not been invited" in r.get_data(as_text=True)
    assert client.get("/a/").status_code == 302     # still logged out


def test_a_google_address_on_an_account_signs_in(google_app, monkeypatch):
    from harelphotos import google_auth as ga
    from harelphotos import users as users_mod

    app, cfg = google_app
    # Attach a Google address to the existing local account.
    us = users_mod.load(cfg.users_file)
    table = dict(us.by_token)
    table["nyh"] = replace(table["nyh"], google="nyh@gmail.com")
    users_mod.save(cfg.users_file, users_mod.Users(by_token=table))

    client = app.test_client()
    client.get("/auth/google")
    monkeypatch.setattr(ga, "complete",
                        lambda *a, **k: (ga.Identity("nyh@gmail.com", "9"), "/a/2019/01/"))
    r = client.get("/auth/google/callback?code=x&state=y")
    assert r.status_code == 302
    assert r.headers["Location"] == "/a/2019/01/"
    assert client.get("/a/").status_code == 200     # really logged in


def test_a_cancelled_sign_in_is_not_an_error_page(google_app):
    app, _ = google_app
    r = app.test_client().get("/auth/google/callback?error=access_denied")
    assert r.status_code == 400
    assert "cancelled" in r.get_data(as_text=True).lower()
