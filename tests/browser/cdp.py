"""Shared odds and ends for the browser checks.

Each check drives its own headless Chrome over the DevTools Protocol, and
starts it itself so that it can be run on its own against a server you already
have. What they share lives here.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import socket


def free_port() -> int:
    """A port nobody is listening on, for Chrome's debugging endpoint.

    The checks used to name fixed ports, and two of them named the same one --
    harmless while they run strictly in sequence, and a collision waiting for
    whoever first runs two at once. A fixed port also fails for a reason that
    looks nothing like the cause: Chrome declines to listen, the check waits
    the full thirty seconds and reports that the browser did not start, when
    what happened is that something else already had the port.

    The profile directory stays fixed per check, deliberately: that is what
    makes a leaked browser findable by name afterwards, and it is a path
    rather than a number so two checks cannot accidentally share it.

    Strictly this is a race -- the port is free when we look and Chrome binds
    it a moment later. Nothing else here is allocating ports, and the loser
    reports that the browser did not start rather than doing something worse.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
