"""Authentication and the login gate (DESIGN.md 12).

The security-critical tests. The one that matters most is
`test_every_route_either_needs_a_session_or_is_on_the_list`: it walks every
registered route, so a new one cannot quietly forget the gate.
"""

from __future__ import annotations

import time

import pytest

from harelphotos import acl, auth, scanner
from harelphotos.web import create_app

from . import fixtures


@pytest.fixture
def project(tmp_path):
    photos = fixtures.make_tree(tmp_path / "pictures")
    cfg = fixtures.make_config(tmp_path, photos)
    conn = fixtures.fresh_index(cfg)
    scanner.scan(cfg, conn)
    conn.close()
    fixtures.add_user(cfg, "nyh", "nyh", name="Nadav")
    fixtures.add_user(cfg, "sis", "sispw", name="Sis")
    fixtures.add_user(cfg, "boss", "bosspw", name="Boss", admin=True)
    return cfg


@pytest.fixture
def app(project):
    a = create_app(project, require_login=True)
    a.config.update(TESTING=True)
    return a


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def login(client, username="nyh", password="nyh", **extra):
    page = client.get("/login")
    token = _csrf_from(page.get_data(as_text=True))
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf": token, **extra},
        follow_redirects=False,
    )


def _csrf_from(html: str) -> str:
    import re

    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no CSRF token in the form"
    return m.group(1)


# ------------------------------------------------------------- the gate

def test_every_route_either_needs_a_session_or_is_on_the_list(app):
    """The whole point of the gate. Walk every route and check.

    This is the test that stops a new endpoint quietly becoming public.
    """
    client = app.test_client()
    examples = {
        "album": "/a/2019/01/",
        "photo": "/p/2019/01/a.jpg",
        "image": "/i/512/2019/01/a.jpg",
        "inline_original": "/i/orig/2019/01/a.jpg",
        "original": "/orig/2019/01/a.jpg",
        "api_album": "/api/a/2019/01/",
        "landing": "/",
        "login": "/login",
        "privacy": "/privacy",
        "terms": "/terms",
        "healthz": "/healthz",
        "public_asset": "/public/landing-640.avif",
        "static": "/static/app.css",
        "logout": None,          # POST-only; covered by its own tests
    }
    checked = 0
    for rule in app.url_map.iter_rules():
        endpoint = rule.endpoint
        if endpoint not in examples:
            pytest.fail(
                f"route {endpoint!r} ({rule}) is not covered by this test. Add it, "
                f"and decide deliberately whether it belongs in PUBLIC_ENDPOINTS."
            )
        url = examples[endpoint]
        if url is None:
            continue
        checked += 1
        r = client.get(url)
        if endpoint in auth.PUBLIC_ENDPOINTS:
            assert r.status_code in (200, 404), f"{endpoint} should be reachable: {r.status_code}"
        else:
            assert r.status_code in (302, 401), (
                f"{endpoint} answered {r.status_code} with no session — it must "
                f"redirect to the login page or refuse"
            )
    assert checked > 8


def test_album_redirects_to_login_when_logged_out(client):
    r = client.get("/a/2019/01/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_images_refuse_rather_than_redirect(client):
    """A stale page's image requests should not be answered with HTML."""
    for url in ("/i/512/2019/01/a.jpg", "/orig/2019/01/a.jpg", "/api/a/2019/01/"):
        assert client.get(url).status_code == 401, url


def test_public_pages_need_no_session(client):
    for url in ("/", "/login", "/privacy", "/terms", "/healthz", "/static/app.css"):
        assert client.get(url).status_code == 200, url


def test_landing_page_shows_the_login_form(client):
    body = client.get("/").get_data(as_text=True)
    assert "By invitation only" in body
    assert 'name="password"' in body
    assert 'name="csrf"' in body


# ------------------------------------------------------------------ login

def test_successful_login(client):
    r = login(client)
    assert r.status_code == 302
    assert r.headers["Location"] == "/a/"
    assert client.get("/a/2019/01/").status_code == 200


def test_wrong_password_is_refused(client):
    r = login(client, password="wrong")
    assert r.status_code == 401
    assert "Incorrect username or password" in r.get_data(as_text=True)
    assert client.get("/a/2019/01/").status_code == 302


def test_unknown_user_says_exactly_the_same_thing(client):
    """Never reveal which accounts exist."""
    a = login(client, username="ghost", password="x").get_data(as_text=True)
    b = login(client, username="nyh", password="x").get_data(as_text=True)
    assert "Incorrect username or password" in a
    assert "Incorrect username or password" in b


def test_login_without_csrf_is_refused(client):
    r = client.post("/login", data={"username": "nyh", "password": "nyh"})
    assert r.status_code == 400
    assert client.get("/a/2019/01/").status_code == 302


def test_login_with_a_stale_csrf_token_is_refused(client):
    client.get("/login")
    r = client.post(
        "/login", data={"username": "nyh", "password": "nyh", "csrf": "not-the-token"}
    )
    assert r.status_code == 400


def test_landing_redirects_once_logged_in(client):
    login(client)
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"] == "/a/"


# ----------------------------------------------------------------- logout

def test_logout_clears_the_session(client):
    login(client)
    page = client.get("/a/").get_data(as_text=True)
    r = client.post("/logout", data={"csrf": _csrf_from(page)})
    assert r.status_code == 302
    assert client.get("/a/2019/01/").status_code == 302


def test_get_logout_does_not_log_you_out(client):
    """Link prefetchers, mail scanners and preview bots all follow GET links."""
    login(client)
    assert client.get("/logout").status_code == 405
    assert client.get("/a/2019/01/").status_code == 200


def test_logout_without_csrf_is_refused(client):
    login(client)
    assert client.post("/logout", data={}).status_code == 400
    assert client.get("/a/2019/01/").status_code == 200


def test_every_logged_in_page_offers_a_way_out(client):
    login(client)
    for url in ("/a/", "/a/2019/01/", "/p/2019/01/a.jpg"):
        body = client.get(url).get_data(as_text=True)
        assert 'action="/logout"' in body, url


def test_the_footer_names_who_you_are(client):
    login(client)
    body = client.get("/a/").get_data(as_text=True)
    assert "Nadav" in body
    assert "you are logged in as" in body


# ------------------------------------------------------------- revocation

def test_bumping_epoch_invalidates_an_existing_session(project, client):
    """For a lost phone, or a relative who should no longer have access."""
    login(client)
    assert client.get("/a/2019/01/").status_code == 200

    from harelphotos import users as users_mod

    us = users_mod.load(project.users_file)
    table = dict(us.by_token)
    old = table["nyh"]
    table["nyh"] = users_mod.User(
        token=old.token, name=old.name, password_hash=old.password_hash,
        admin=old.admin, epoch=old.epoch + 1,
    )
    users_mod.save(project.users_file, users_mod.Users(by_token=table))

    assert client.get("/a/2019/01/").status_code == 302


def test_deleting_a_user_invalidates_their_session(project, client):
    login(client)
    from harelphotos import users as users_mod

    users_mod.save(project.users_file, users_mod.Users(by_token={}))
    assert client.get("/a/2019/01/").status_code == 302


def test_a_tampered_cookie_is_rejected(client):
    login(client)
    client.set_cookie("session", "forged-value", domain="localhost")
    assert client.get("/a/2019/01/").status_code == 302


# ------------------------------------------------------------ throttling

def test_repeated_failures_are_throttled(client):
    for _ in range(auth.MAX_FAILURES_BEFORE_DELAY + 1):
        login(client, password="wrong")
    r = login(client, password="wrong")
    assert r.status_code == 429
    assert "Too many attempts" in r.get_data(as_text=True)


def test_a_successful_login_clears_the_throttle(client):
    # One short of the threshold, so the correct password is not itself delayed.
    for _ in range(auth.MAX_FAILURES_BEFORE_DELAY - 1):
        login(client, password="wrong")
    assert login(client).status_code == 302      # correct password still works

    # Log out, so /login serves the form again rather than redirecting.
    page = client.get("/a/").get_data(as_text=True)
    client.post("/logout", data={"csrf": _csrf_from(page)})

    # The counter was cleared, so a fresh mistake is refused, not delayed.
    assert login(client, password="wrong").status_code == 401


# ------------------------------------------------------- next= redirects

@pytest.mark.parametrize(
    "raw",
    ["https://evil.example/", "//evil.example", "javascript:alert(1)",
     "http://127.0.0.1:5000/a/", "/\\evil.example", "not-a-path"],
)
def test_open_redirects_are_refused(raw):
    assert auth.safe_next(raw) is None, raw


@pytest.mark.parametrize("raw", ["/a/2019/01/", "/p/x.jpg", "/a/a%20b/"])
def test_ordinary_paths_are_accepted(raw):
    assert auth.safe_next(raw) == raw


def test_a_deep_link_survives_logging_in(client):
    """A link sent to family must work even when they are logged out."""
    r = client.get("/a/2019/01/")
    assert "next=/a/2019/01/" in r.headers["Location"]

    page = client.get("/login?next=/a/2019/01/").get_data(as_text=True)
    r = client.post("/login", data={
        "username": "nyh", "password": "nyh",
        "csrf": _csrf_from(page), "next": "/a/2019/01/",
    })
    assert r.headers["Location"] == "/a/2019/01/"


def test_a_hostile_next_is_dropped_rather_than_followed(client):
    page = client.get("/login").get_data(as_text=True)
    r = client.post("/login", data={
        "username": "nyh", "password": "nyh",
        "csrf": _csrf_from(page), "next": "https://evil.example/",
    })
    assert r.headers["Location"] == "/a/"


# -------------------------------------------------------------------- ACL

def test_a_restricted_album_is_hidden_from_others(client):
    """`private/` allows only nyh."""
    login(client, "sis", "sispw")
    assert client.get("/a/").status_code == 200
    assert "/a/private/" not in client.get("/a/").get_data(as_text=True)
    # 404 rather than 403: do not confirm that a private album exists.
    assert client.get("/a/private/").status_code == 404
    assert client.get("/p/private/secret.jpg").status_code == 404


def test_the_permitted_user_sees_it(client):
    login(client, "nyh", "nyh")
    assert "/a/private/" in client.get("/a/").get_data(as_text=True)
    assert client.get("/a/private/").status_code == 200
    assert client.get("/p/private/secret.jpg").status_code == 200


def test_an_admin_sees_everything(client):
    login(client, "boss", "bosspw")
    assert client.get("/a/private/").status_code == 200


def test_a_private_albums_images_are_not_fetchable_by_guessing(client):
    """The check must be on the image routes, not only the HTML (DESIGN.md 6)."""
    login(client, "sis", "sispw")
    for url in (
        "/i/512/private/secret.jpg",
        "/i/orig/private/secret.jpg",
        "/orig/private/secret.jpg",
        "/api/a/private/",
    ):
        assert client.get(url).status_code == 404, url


def test_groups_are_expanded_at_request_time(project):
    """Editing a group in config.toml takes effect without a rescan."""
    photos = project.photo_root
    (photos / "private" / ".album.toml").write_text(
        'allow = ["@family"]\n', encoding="utf-8"
    )
    from harelphotos import db

    conn = db.open_index(project.index_db)
    scanner.scan(project, conn)
    conn.close()

    # No group defined yet: nobody but an admin gets in.
    app = create_app(project, require_login=True)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        login(c, "sis", "sispw")
        assert c.get("/a/private/").status_code == 404

    # Define the group; same index, no rescan.
    object.__setattr__(project, "groups", {"family": ("sis",)})
    app = create_app(project, require_login=True)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        login(c, "sis", "sispw")
        assert c.get("/a/private/").status_code == 200


# ------------------------------------------------------- the no-login mode

def test_no_login_mode_lets_everything_through(project):
    app = create_app(project, require_login=False)
    app.config.update(TESTING=True)
    c = app.test_client()
    assert c.get("/a/").status_code == 200
    assert c.get("/a/private/").status_code == 200      # admin-equivalent
    assert c.get("/i/512/2019/01/a.jpg").status_code == 200


def test_no_login_mode_says_so_on_the_page(project):
    """A window left open for a week must not be mistaken for the real thing."""
    app = create_app(project, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/").get_data(as_text=True)
    assert "Running without a login" in body


def test_no_login_mode_does_not_pretend_someone_is_logged_in(project):
    app = create_app(project, require_login=False)
    app.config.update(TESTING=True)
    body = app.test_client().get("/a/").get_data(as_text=True)
    assert 'action="/logout"' not in body
