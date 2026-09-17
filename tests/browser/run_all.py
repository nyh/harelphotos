#!/usr/bin/env python3
"""Run every browser check, against an album built for the purpose.

    .venv/bin/python tests/browser/run_all.py

That is the whole invocation. It creates a throwaway photo tree, indexes it,
serves it on a free port, runs each check in `tests/browser/` against it, and
takes all of it down again. Nothing touches your own collection, your config,
or the network, and `--keep` leaves the tree in place if you want to poke at it
in a real browser afterwards.

**Not part of the pytest suite, and deliberately so.** These need Chrome and
take the better part of a minute each, which is too slow to pay on every run.
The cost of that decision is the one this script exists to reduce: a check
nobody runs stops being true without anyone noticing. `check_nav.py` spent some
time asserting that swiping down returned to the album, months after the
gesture was deliberately removed — it was not wrong when it was written, and
nothing ran it to say otherwise.

So: run this after touching `app.js`, `app.css`, or anything about how a page
is laid out or navigated. It is the only thing here that can see what a browser
actually does — history entries, justified row widths, resolved colours, scroll
restoration — none of which the pytest suite can observe at all.

Needs Chrome and `pip install websocket-client`.

Copyright (C) 2026 Nadav Har'El
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# The console script beside whichever interpreter is running this, so the
# whole thing works from the venv without anything on PATH.
HP = str(Path(sys.executable).parent / "harelphotos")

# Enough photographs, in enough shapes, for the checks to have something to
# measure: justified rows need varied aspect ratios to be worth laying out, and
# scroll restoration needs a page several windows tall, or the browser clamps
# the scroll and the positions being compared are all the same one.
SHAPES = [(800, 600), (600, 800), (1200, 500), (900, 900), (1000, 700)]
COUNT = 90

# Every check, and how it wants to be called. A URL means the album; the dark
# one takes the site root and the album path separately.
CHECKS = [
    ("check_layout.py", "album"),
    ("check_nav.py", "album"),
    ("check_scroll.py", "album"),
    ("check_dark.py", "base+path"),
]

# Left behind by the checks themselves, which each start their own browser.
PROFILES = ["/tmp/cdp-profile-nav", "/tmp/cdp-profile", "/tmp/cdp-layout",
            "/tmp/cdp-dark"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_album(root: Path) -> Path:
    """A photo tree, a config and an index, all under `root`."""
    sys.path.insert(0, str(ROOT))
    from tests import fixtures                       # noqa: E402

    photos = root / "pictures"
    for i in range(COUNT):
        fixtures.make_jpeg(
            photos / "album" / f"p{i:03d}.jpg",
            size=SHAPES[i % len(SHAPES)],
            taken=f"2019:08:{i % 28 + 1:02d} 12:00:00",
            color=(40 + (i * 37) % 200, 60 + (i * 53) % 180, 90 + (i * 29) % 160),
        )
    cfg = root / "config.toml"
    run([HP, "-c", str(cfg), "init",
         "--photo-root", str(photos), "--state-dir", str(root / "state")])
    env = dict(os.environ, HARELPHOTOS_CONFIG=str(cfg))
    run([HP, "-q", "scan"], env=env)
    return cfg


def run(cmd: list[str], env: dict | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")


def wait_for(url: str, seconds: float = 20) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read(1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true",
                    help="leave the throwaway album in place and say where")
    ap.add_argument("--only", metavar="NAME",
                    help="run just the checks whose filename contains NAME")
    args = ap.parse_args()

    if not shutil.which("google-chrome"):
        sys.exit("google-chrome is not installed; these checks need a real browser")
    try:
        import websocket                              # noqa: F401
    except ImportError:
        sys.exit("pip install websocket-client (not a runtime dependency)")

    root = Path(tempfile.mkdtemp(prefix="harelphotos-browser-"))
    print(f"building a throwaway album in {root} ...")
    cfg = build_album(root)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    album = f"{base}/a/album/"
    serve = subprocess.Popen(
        [HP, "serve", "--port", str(port)],
        env=dict(os.environ, HARELPHOTOS_CONFIG=str(cfg)),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)

    failed: list[str] = []
    try:
        if not wait_for(album):
            sys.exit(f"the server did not come up on {port}")
        print(f"serving {album}\n")

        for name, style in CHECKS:
            if args.only and args.only not in name:
                continue
            argv = [album] if style == "album" else [base, "/a/album/"]
            print(f"──────── {name}")
            r = subprocess.run([sys.executable, str(HERE / name), *argv], cwd=ROOT)
            if r.returncode != 0:
                failed.append(name)
            print()
    finally:
        serve.terminate()
        try:
            serve.wait(timeout=10)
        except subprocess.TimeoutExpired:
            serve.kill()
        # The checks each start and stop their own browser; what they leave is
        # the profile directory, which is pure litter.
        for p in PROFILES:
            shutil.rmtree(p, ignore_errors=True)
        if args.keep:
            print(f"kept the album in {root}")
            print(f"  HARELPHOTOS_CONFIG={cfg} {HP} serve --port {port}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("every browser check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
