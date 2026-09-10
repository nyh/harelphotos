"""Shared test setup.

The guard below exists because of a real incident: a test invoked the `serve`
subcommand after it stopped being a stub, started a genuine Flask server, and
blocked forever holding port 5000 — long after the test run had been abandoned.
Nothing in the suite has any business listening on a TCP port, so make it
impossible rather than rely on remembering.

Deliberately scoped to `listen()` on TCP: multiprocessing's worker pool binds
unix sockets of its own, and those are none of our business.

A test that genuinely needs a server -- `test_envcheck_live.py`, which checks
redirect handling and so cannot use a mocked response -- opts in by requesting
the `local_server_allowed` fixture. That keeps the ban the default while
leaving one deliberate, visible way through it.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

import socket

import pytest


@pytest.fixture
def local_server_allowed():
    """Opt out of the ban above, for a test that really must listen.

    Requesting this fixture is the whole mechanism: `_no_tcp_listeners` checks
    for it. Anything using it must bind 127.0.0.1 on port 0, serve from a
    daemon thread, and shut down in a finally/fixture teardown.
    """
    return True


@pytest.fixture(autouse=True)
def _no_tcp_listeners(monkeypatch, request):
    if "local_server_allowed" in request.fixturenames:
        yield
        return

    real_listen = socket.socket.listen

    def refuse(self, *args):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(
                "a test tried to listen on a TCP port. Tests must not start a "
                "server: it blocks forever and orphans a process holding the "
                "port. If a real server is genuinely needed, request the "
                "`local_server_allowed` fixture and shut it down in teardown."
            )
        return real_listen(self, *args)

    monkeypatch.setattr(socket.socket, "listen", refuse)
    yield
