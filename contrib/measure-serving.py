#!/usr/bin/env python3
"""Measure how fast a running harelphotos actually serves.

Usage
-----

    contrib/measure-serving.py BASE_URL [--user U] [--album PATH] [--heavy]

    BASE_URL   required. The site root, e.g. https://photos.example.org --
               exactly as a browser would reach it, since a redirect to the
               canonical name would add a round trip to every figure.
    --user U   the account to log in as. Defaults to $USER.
    --album P  which album to pull thumbnails from, e.g. 2016/03. Defaults to
               searching for one with enough photographs to fill a screen,
               which is what makes the throughput figure meaningful. Pin it to
               one album when comparing runs over days: the search is
               deterministic but its answer moves as photographs are added, and
               a screenful of 2003 snapshots is not the same number of
               kilobytes as one of 2016 photographs.
    --heavy    also measure sustained throughput -- a whole album's thumbnails,
               then original photographs, about 8 MB in all. Turn it on when
               you need to know whether a slow screenful is the uplink or the
               serving path, and when you want a throughput figure steady
               enough to compare between days. The screenful stays a screenful
               either way: it answers a different question, which is how long
               somebody waits for the grid to appear.

    $HARELPHOTOS_PASSWORD  the password. If unset you are prompted for it.
                           Either way it is never written to disk, and the
                           session cookie lives in a private temporary file
                           that is removed on exit. Prefer the prompt on a
                           shared machine, where the environment of a running
                           process is not as private as it looks.

Needs `curl` built with HTTP/2 -- `curl --version | grep HTTP2` -- because
that is what a browser will use, and HTTP/1.1 figures do not transfer.

What it is for
--------------

Answers the question "the site feels slow -- what is slow?" by separating the
four things that can be responsible, because they need completely different
fixes and they are easy to confuse:

  latency     one round trip. Distance. Nothing on the server can improve it.
  per-request the server's own work per request: the index lookup, the access
              check, opening the file.
  throughput  bytes per second once they start flowing. The uplink, or the
              CPU if something copies every byte.
  stalls      requests that take far longer than their neighbours, which are
              usually a bug rather than a load.

A worked example of why the separation matters. In September 2026 an album
page took six and a half seconds for one screenful. It looked like a slow
server, and the request count looked like the cause. It was neither: latency
was perfect and per-request work was cheap, but *throughput* through the
X-Sendfile hand-off was 52 KB/s against Python's 407 KB/s on the same
connection. Only the split made that visible. A month's worth of plausible
theories -- packing thumbnails into one file, HTTP/3, a bigger cache -- would
all have been wasted effort.

Copyright (C) 2026 Nadav Har'El
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import argparse
import atexit
import getpass
import os
import re
import shutil
import statistics as st
import subprocess
import sys
import tempfile
import time

# How long a request must take before it counts as a stall rather than as a
# slow request. Comfortably above one round trip on any real link, and well
# below the ~1s stalls that the X-Sendfile hand-off used to produce.
STALL_MS = 300

# Enough thumbnails to fill a screen on a desktop, which is the unit of work a
# person actually waits for.
SCREENFUL = 24

# When to stop looking for an album and settle for the best one found.
#
# Stopping at the first album with a bare screenful is a bad rule: most albums
# hold a hundred or more, and the first to scrape past 24 is an oddity -- a
# short month, a handful of scanned snapshots -- whose photographs are not
# typical of the collection. Keep looking until one is comfortably bigger, then
# take a screenful from that.
GOOD_ENOUGH = SCREENFUL * 3

# How many thumbnails the --heavy sustained test asks for. A screenful is too
# small to measure throughput honestly over a long link: 400 KB is gone before
# TCP is out of slow start, which is why a screenful's time bounces by 15%
# between runs while this figure holds steady. This is also the most realistic
# bulk workload the site has -- somebody scrolling a long album.
BULK = 120

# Repeats of the whole-screenful test. Three is enough to see whether a figure
# is stable; the first is often slower because the connection is still in TCP
# slow start, so the median is reported rather than the mean.
REPEATS = 3


def curl(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["curl", "-sS", *args], capture_output=True,
                          text=True, timeout=timeout)


class Session:
    """A logged-in cookie jar, and the helpers that use it."""

    def __init__(self, base: str, jar: str) -> None:
        self.base = base.rstrip("/")
        self.jar = jar

    def login(self, user: str, password: str) -> None:
        # The form carries a CSRF token tied to the session cookie, so the GET
        # that fetches the token must share a jar with the POST that uses it.
        page = curl(["-c", self.jar, "-b", self.jar, f"{self.base}/login"]).stdout
        m = re.search(r'name="csrf"\s+value="([^"]+)"', page)
        if not m:
            sys.exit(f"no login form at {self.base}/login -- is this a harelphotos site?")
        r = curl(["-c", self.jar, "-b", self.jar, "-o", "/dev/null",
                  "-w", "%{http_code}", "-X", "POST",
                  "--data-urlencode", f"username={user}",
                  "--data-urlencode", f"password={password}",
                  "--data-urlencode", f"csrf={m.group(1)}",
                  f"{self.base}/login"])
        if r.stdout.strip() not in ("200", "302", "303"):
            sys.exit(f"login failed (HTTP {r.stdout.strip()}) -- wrong password?")

    def config_file(self, urls: list[str]) -> str:
        """curl applies -o to the first URL only, and silently writes the rest
        to stdout -- which corrupts the timing output and is invisible until
        the numbers make no sense. A config file gives every URL its own
        output, and is the only safe way to pass a list."""
        fd, path = tempfile.mkstemp(prefix="harelphotos-urls-", suffix=".txt")
        with os.fdopen(fd, "w") as f:
            for u in urls:
                f.write(f'url = "{u}"\noutput = "/dev/null"\n')
        atexit.register(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def sequential(self, urls: list[str]) -> list[float]:
        """One request at a time, all on one kept-alive connection.

        Isolates latency and per-request work from throughput: nothing is
        competing, so each figure is one round trip plus whatever the server
        did. Note the first result is dropped -- it carries the TCP and TLS
        handshake, which would otherwise dominate the mean.
        """
        cfg = self.config_file(urls)
        r = curl(["-b", self.jar, "-H", ACCEPT, "-K", cfg,
                  "-w", "%{time_starttransfer}\n"])
        out = [float(x) * 1000 for x in r.stdout.split() if x]
        return out[1:]

    def parallel(self, urls: list[str], concurrency: int) -> tuple[float, int]:
        """Many at once over one multiplexed HTTP/2 connection, as a browser
        does. Returns wall-clock seconds and bytes."""
        cfg = self.config_file(urls)
        t0 = time.monotonic()
        r = curl(["--http2", "--parallel", "--parallel-max", str(concurrency),
                  "-b", self.jar, "-H", ACCEPT, "-K", cfg,
                  "-o", "/dev/null", "-w", "%{size_download}\n"])
        elapsed = time.monotonic() - t0
        return elapsed, sum(int(x) for x in r.stdout.split() if x.isdigit())


ACCEPT = "Accept: image/avif,image/webp,*/*"


def album_thumbs(s: Session, album: str) -> tuple[list[str], list[str]]:
    """The thumbnails on one album page, and the albums below it."""
    body = curl(["-b", s.jar, f"{s.base}/a/{album.strip('/')}/"]).stdout
    thumbs: list[str] = []
    for m in re.finditer(r'srcset="([^"]*)"', body):
        for cand in m.group(1).split(", "):
            u = cand.rsplit(" ", 1)[0]
            if "/i/512/" in u:
                thumbs.append(s.base + u)
    children = [m.group(1) for m in re.finditer(r'href="/a/([^"]+)/"', body)]
    return thumbs, children


def find_album(s: Session, album: str) -> tuple[str, list[str]]:
    """Pick an album with enough photographs to fill a screen.

    Taking the first album listed is not good enough: on a tree organised by
    year the first is often a handful of scanned photographs, and a "screenful"
    of one thumbnail measures nothing at all -- it reports the round trip and
    calls it throughput. So walk down until there are enough, and keep the best
    candidate in case nothing has a full screen.
    """
    if album:
        thumbs, _ = album_thumbs(s, album)
        if not thumbs:
            sys.exit(f"no thumbnails on /a/{album}/ -- wrong album, "
                     "or its images are not generated yet")
        return album, thumbs

    best: tuple[str, list[str]] = ("", [])
    queue, seen = [""], set()
    while queue and len(best[1]) < GOOD_ENOUGH:
        # A budget, because a large tree has thousands of albums and one good
        # one is all we need.
        if len(seen) > 40:
            break
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        thumbs, children = album_thumbs(s, current)
        if len(thumbs) > len(best[1]):
            best = (current, thumbs)
        # Depth first. A tree organised by year has a couple of dozen album-of-
        # album pages at the top, each holding no photographs at all, so going
        # breadth first spends the whole budget on them and never reaches a
        # month. Descending finds real photographs in two or three fetches.
        queue = children + queue

    if not best[1]:
        sys.exit("found no album with generated thumbnails; pass --album")
    if len(best[1]) < SCREENFUL:
        print(f"note: best album found has only {len(best[1])} thumbnails; "
              f"pass --album for a fuller one\n", file=sys.stderr)
    return best[0], best[1]


def row(label: str, value: str, note: str = "") -> None:
    print(f"  {label:<40} {value:>10}   {note}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("base", help="site root, e.g. https://photos.example.org")
    p.add_argument("--user", default=os.environ.get("USER", ""))
    p.add_argument("--album", default="", help="album to pull thumbnails from")
    p.add_argument("--heavy", action="store_true",
                   help="also measure bulk throughput (downloads ~6 MB of originals)")
    args = p.parse_args()

    if not shutil.which("curl"):
        sys.exit("curl is not installed")
    if "HTTP2" not in curl(["--version"]).stdout:
        print("warning: this curl has no HTTP/2; figures will not match a browser\n",
              file=sys.stderr)

    password = os.environ.get("HARELPHOTOS_PASSWORD") or getpass.getpass(
        f"password for {args.user}: ")

    fd, jar = tempfile.mkstemp(prefix="harelphotos-jar-")
    os.close(fd)
    os.chmod(jar, 0o600)
    atexit.register(lambda: os.path.exists(jar) and os.unlink(jar))

    s = Session(args.base, jar)
    s.login(args.user, password)

    album, found = find_album(s, args.album)
    thumbs = found[:SCREENFUL]
    relpath = thumbs[0].split("/i/512/", 1)[1].split("?")[0]

    where = f"/a/{album}/" if album else "/a/ (the top album)"
    # Say both numbers. Printing only the sample size reads as though a
    # 24-photograph album had been chosen, when the album may hold hundreds and
    # 24 is deliberately a screenful -- the unit a person actually waits for.
    print(f"\n{s.base}  ({where}, {len(thumbs)} of {len(found)} thumbnails "
          f"-- one screenful)\n")

    # ---- latency, and the server's own work on top of it.
    #
    # The floor is the *fastest* request seen, not a separate cheap endpoint.
    # /healthz was the obvious baseline and is a bad one: measured against the
    # live server it ran 30 ms slower than a thumbnail, which made the image
    # route look like it cost negative time. A request cannot beat the round
    # trip, so the quickest of a few dozen is the round trip plus the couple of
    # milliseconds the server could not avoid -- self-calibrating, and never
    # nonsense.
    seq = s.sequential(thumbs * 3)
    floor = min(seq)
    over = sum(1 for x in seq if x > STALL_MS)
    row("round trip (fastest seen)", f"{floor:.0f} ms",
        "the distance; unfixable from here")
    row("thumbnail, typical", f"{st.median(seq):.0f} ms",
        f"{st.median(seq) - floor:+.0f} ms of server work")
    row(f"  ... stalled (>{STALL_MS} ms)", f"{over * 100 / len(seq):.0f}%",
        "anything above ~1% is a bug, not load")

    # ---- throughput: what a person actually waits for.
    runs = [s.parallel(thumbs, SCREENFUL) for _ in range(REPEATS)]
    best = sorted(runs, key=lambda r: r[0])[len(runs) // 2]
    secs, nbytes = best
    # The byte count, not just the time: a screenful of 2003 snapshots and one
    # of 2016 photographs are not the same number of kilobytes, so the seconds
    # are only comparable between runs over the same album. Printing both makes
    # a mismatched comparison obvious instead of silently misleading.
    row(f"screenful ({len(thumbs)} thumbnails, {nbytes / 1024:.0f} KB)",
        f"{secs:.2f} s", f"{nbytes / 1024 / secs:.0f} KB/s, median of {REPEATS}")

    if args.heavy:
        # Scrolling a long album: many small files, the same route and the same
        # multiplexed connection as the screenful, but enough of them that the
        # figure is throughput rather than slow start.
        bulk = found[:BULK]
        if len(bulk) > SCREENFUL:
            secs, nbytes = s.parallel(bulk, SCREENFUL)
            row(f"whole album ({len(bulk)} thumbnails, {nbytes / 1024:.0f} KB)",
                f"{secs:.2f} s", f"{nbytes / 1024 / secs:.0f} KB/s sustained")

    if args.heavy and relpath:
        one = curl(["--http2", "-b", jar, f"{s.base}/orig/{relpath}",
                    "-o", "/dev/null", "-w", "%{speed_download}"])
        row("one original, single stream", f"{float(one.stdout) / 1024:.0f} KB/s",
            "one connection's share")
        secs, nbytes = s.parallel([f"{s.base}/orig/{relpath}"] * 4, 4)
        row("four originals, in parallel", f"{nbytes / 1024 / secs:.0f} KB/s",
            "closest thing to the uplink's capacity")

    print("""
Reading it:
  Latency high, everything else fine .... distance. HTTP/2 already helps most.
  Per-request cost high ................. the index lookup or the access check;
                                          profile the image route.
  Stalls above a percent or two ......... a bug. They cluster on a clock when
                                          something periodic is to blame.
  Screenful slow, per-request fine ...... throughput. Compare against the
                                          originals below (--heavy): if those
                                          are slow too it is the uplink, and if
                                          they are fast it is the serving path.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
