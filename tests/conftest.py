"""Shared test setup.

The guard below exists because of a real incident: a test invoked the `serve`
subcommand after it stopped being a stub, started a genuine Flask server, and
blocked forever holding port 5000 — long after the test run had been abandoned.
Nothing in the suite has any business listening on a TCP port, so make it
impossible rather than rely on remembering.

Deliberately scoped to `listen()` on TCP: multiprocessing's worker pool binds
unix sockets of its own, and those are none of our business.
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_tcp_listeners(monkeypatch):
    real_listen = socket.socket.listen

    def refuse(self, *args):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(
                "a test tried to listen on a TCP port. Tests must not start a "
                "server: it blocks forever and orphans a process holding the port."
            )
        return real_listen(self, *args)

    monkeypatch.setattr(socket.socket, "listen", refuse)
    yield
