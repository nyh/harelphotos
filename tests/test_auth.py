"""Authentication and the login gate (DESIGN.md 12).

The security-critical tests. The one that matters most is
`test_every_route_either_needs_a_session_or_is_on_the_list`: it walks every
registered route, so a new one cannot quietly forget the gate.
"""

from __future__ import annotations

import re
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
        # Public by necessity: the browser fetches it before anyone signs in,
        # and it holds nothing but the site name and icon paths.
        "manifest": "/manifest.webmanifest",
        "static": "/static/app.css",
        "logout": None,          # POST-only; covered by its own tests
        # POST-only, admin-only, CSRF-checked; covered by test_overrides.py.
        "set_cover": None,
        # Public by necessity: signing in cannot require being signed in.
        # Both 404 when Google sign-in is disabled, which is the fixture here.
        "google_start": "/auth/google",
        "google_callback": "/auth/google/callback",
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


def test_every_page_names_who_you_are_and_offers_a_way_out(client):
    """DESIGN.md 11.3: there must never be a page you cannot leave from.

    This used to be a footer as well as the top bar, which said the same thing
    twice and cost a strip of every screen. The guarantee is what matters, not
    where it lives, so it is asserted on both kinds of page.
    """
    login(client)
    for url in ("/a/", "/a/2019/01/", "/p/2019/01/a.jpg"):
        body = client.get(url).get_data(as_text=True)
        assert "Nadav" in body, url
        assert "Log out" in body, url
        assert url_for_logout(body), f"{url}: no form posting to /logout"


def url_for_logout(body: str) -> bool:
    return 'action="/logout"' in body


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


def test_logging_in_works_while_a_scan_holds_the_index(project):
    """Reported from real use: logging in failed with 'database is locked'.

    SQLite permits one writer at a time. The login throttle was the only thing
    the web process wrote, and it lived in the index — so a running scan made
    logging in impossible. It now has its own database.
    """
    import sqlite3

    scan_writer = sqlite3.connect(project.index_db)
    try:
        scan_writer.execute("PRAGMA journal_mode = WAL")
        scan_writer.execute("BEGIN IMMEDIATE")          # as a scan does
        scan_writer.execute("UPDATE photos SET title = 'mid-scan'")

        app = create_app(project, require_login=True)
        app.config.update(TESTING=True)
        with app.test_client() as c:
            assert login(c).status_code == 302          # logging in still works
            assert c.get("/a/2019/01/").status_code == 200
            # And so does a failed attempt, which is the path that writes.
            page = c.get("/a/").get_data(as_text=True)
            c.post("/logout", data={"csrf": _csrf_from(page)})
            assert login(c, password="wrong").status_code == 401
    finally:
        scan_writer.rollback()
        scan_writer.close()


def test_a_login_succeeds_even_if_the_throttle_cannot_be_written(project, monkeypatch):
    """Never refuse a correct password because a counter could not be saved."""
    import contextlib

    @contextlib.contextmanager
    def broken(cfg):
        yield None                                      # as an unopenable db does

    monkeypatch.setattr(auth, "throttle_db", broken)
    app = create_app(project, require_login=True)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        assert login(c).status_code == 302
        assert c.get("/a/2019/01/").status_code == 200


def test_the_throttle_lives_outside_the_index(project):
    """So a scan and a login never contend for the same writer."""
    app = create_app(project, require_login=True)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        login(c, password="wrong")
    assert (project.state_dir / auth.AUTH_DB).exists()


# ------------------------------------------------- the cookie and image caching
#
# These are about speed, but they live here because the mechanism is the
# session cookie and getting them wrong the other way would leak images between
# people sharing a browser.

def test_the_session_cookie_is_not_resent_on_every_response(client):
    """Otherwise every cached thumbnail is discarded on every page load.

    Image responses carry `Vary: Cookie` -- Flask adds it to anything that
    touched the session, and the access check always does -- so the cookie
    value is part of the browser's cache key. Flask's default re-signs the
    cookie on every response, giving it a fresh value each time and
    invalidating the entire grid. Invisible on localhost; seconds of grey
    placeholders over a real network on every back-navigation.
    """
    login(client)
    resent = [client.get("/a/").headers.get("Set-Cookie") for _ in range(3)]
    assert resent == [None, None, None], (
        f"the session cookie was re-sent on an unchanged session: {resent}"
    )


def test_images_still_vary_on_cookie(client):
    """The fix above must not become 'drop Vary: Cookie'.

    Two people sharing a browser must not be served each other's images from
    the disk cache. Now that the cookie value is stable, keeping this costs
    nothing.
    """
    login(client)
    body = client.get("/a/2019/01/").get_data(as_text=True)
    m = re.search(r'srcset="([^" ]+)', body)
    assert m, "no image in the album page to check"
    r = client.get(m.group(1))
    assert r.status_code == 200
    assert "Cookie" in r.headers.get("Vary", ""), r.headers.get("Vary")


def test_a_changed_session_still_sends_the_cookie(client):
    """The other direction: logging in and out must still take effect."""
    r = login(client)
    assert r.status_code == 302
    assert "session=" in (r.headers.get("Set-Cookie") or "")

    token = _csrf_from(client.get("/a/").get_data(as_text=True))
    out = client.post("/logout", data={"csrf": token})
    assert out.status_code == 302
    assert "session=" in (out.headers.get("Set-Cookie") or "")
    assert client.get("/a/").status_code == 302      # really logged out


# ------------------------------------------- serving files, and only the right ones

def test_the_original_routes_cannot_be_talked_out_of_the_photo_tree(project):
    """Asked when originals stopped being handed to Apache: does the server now
    serve any file on disk?

    No. A path reaches a file only by exact lookup in the index -- an allowlist
    by construction, not a filter -- so anything the scanner did not record
    cannot be named. `_clean_path` refuses "." and ".." segments before that,
    and the ACL check runs after.
    """
    secret = project.photo_root.parent / "secret.txt"
    secret.write_text("PRIVATE KEY MATERIAL\n", encoding="utf-8")
    (project.photo_root / "Picasa.ini").write_text("[Picasa]\n", encoding="utf-8")

    app = create_app(project, require_login=True)
    app.config.update(TESTING=True)
    c = app.test_client()
    login(c)

    for url in ("/orig/../secret.txt",
                "/orig/2019/01/../../../secret.txt",
                "/orig/%2e%2e/secret.txt",
                "/orig/2019/01/%2e%2e/%2e%2e/%2e%2e/secret.txt",
                "/orig/etc/passwd",
                "/i/orig/../secret.txt",
                # Inside the photo tree, but not photographs: only .jpg and
                # .jpeg are indexed, so neither exists as far as this is
                # concerned.
                "/orig/Picasa.ini",
                "/orig/2019/01/.album.toml"):
        r = c.get(url, follow_redirects=True)
        assert r.status_code == 404, f"{url} -> {r.status_code}"
        assert b"PRIVATE" not in r.get_data(), url

    assert c.get("/orig/2019/01/a.jpg").status_code == 200


def test_sending_a_file_outside_the_served_trees_fails_closed(project):
    """Defence in depth, added deliberately when an accidental one was lost.

    Apache refuses anything outside XSendFilePath, which was a second wall
    behind the index lookup until originals stopped being handed to it. This
    is the replacement, so a future route that forgets the lookup fails closed.
    """
    from pathlib import Path

    from harelphotos import images

    secret = project.photo_root.parent / "secret2.txt"
    secret.write_text("PRIVATE KEY MATERIAL\n", encoding="utf-8")

    app = create_app(project, require_login=False)

    @app.route("/careless/<path:p>")
    def careless(p):                       # a mistake, as one would look
        return images.send(project, Path("/") / p, "text/plain", vary_accept=False)

    app.config.update(TESTING=True)
    r = app.test_client().get("/careless" + str(secret))
    assert r.status_code == 404
    assert b"PRIVATE" not in r.get_data()
