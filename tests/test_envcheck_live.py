"""The live-site check (``check --env --url``).

This is the only view of a machine I cannot log in to, and one of its
assertions -- that the collection is not readable without logging in -- is the
most consequential thing the tool says. It once got that exactly backwards:
`urlopen` follows redirects by default, so a correct 302 to the login page was
followed to the login page's own 200 and reported as "the collection is
PUBLIC". A real server is used here because the bug lived entirely in redirect
handling, which a mocked response cannot reproduce.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from harelphotos import envcheck


def serve(routes: dict[str, tuple[int, str]]):
    """A throwaway HTTP server. Returns (base_url, shutdown)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, location = routes.get(self.path, (404, ""))
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]

    def stop():
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)

    return f"http://127.0.0.1:{port}", stop


@pytest.fixture
def site(request, local_server_allowed):
    """Parametrised by the routes the fake site should serve.

    `local_server_allowed` is the deliberate opt-out of conftest's ban on
    listening sockets; the teardown below is what earns it.
    """
    base, stop = serve(request.param)
    try:
        yield base
    finally:
        stop()


def verdict_for(report: envcheck.Report, needle: str) -> str:
    for verdict, what, _ in report.lines:
        if needle in what:
            return verdict
    raise AssertionError(f"no line matching {needle!r} in {report.render()}")


LOCKED = {
    "/": (200, ""),
    "/a/": (302, "/login?next=/a/"),
    "/api/a/": (401, ""),
    "/login": (200, ""),
}

PUBLIC = {
    "/": (200, ""),
    "/a/": (200, ""),
    "/api/a/": (200, ""),
}


@pytest.mark.parametrize("site", [LOCKED], indirect=True)
def test_a_redirect_to_login_counts_as_locked_down(site):
    """The regression. This is what a correctly configured site does, and it
    was being reported as PUBLIC."""
    r = envcheck.Report()
    envcheck._live(r, site)
    assert verdict_for(r, "login required (album)") == envcheck.OK
    assert verdict_for(r, "login required (album JSON)") == envcheck.OK
    assert "PUBLIC" not in r.render()


@pytest.mark.parametrize("site", [PUBLIC], indirect=True)
def test_a_genuinely_public_album_is_reported(site):
    """The other direction matters just as much: this must not go quiet."""
    r = envcheck.Report()
    envcheck._live(r, site)
    assert verdict_for(r, "login required (album)") == envcheck.FAIL
    assert verdict_for(r, "login required (album JSON)") == envcheck.FAIL
    assert r.failures >= 2
    assert "WITHOUT LOGGING IN" in r.render()


@pytest.mark.parametrize("site", [{"/": (200, ""), "/a/": (401, ""),
                                   "/api/a/": (401, "")}], indirect=True)
def test_a_flat_refusal_also_counts_as_locked_down(site):
    r = envcheck.Report()
    envcheck._live(r, site)
    assert verdict_for(r, "login required (album)") == envcheck.OK


def test_an_unreachable_site_does_not_claim_it_is_public(local_server_allowed):
    """Failing to connect must never be mistaken for a passing lock check."""
    base, stop = serve({})
    stop()                     # nothing is listening now
    r = envcheck.Report()
    envcheck._live(r, base)
    assert "PUBLIC" not in r.render()
    assert r.failures >= 1
