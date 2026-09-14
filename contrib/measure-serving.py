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
    --heavy    also measure sustained throughput -- a long album's thumbnails,
               then original photographs. Turn it on when you scroll quickly
               through big albums, when you need to know whether a slow
               screenful is the uplink or the serving path, or when you want a
               figure steady enough to compare between days.
    --bulk N   how many thumbnails the sustained test asks for, default 120.
               Set it to the size of the albums you actually scroll: `--bulk
               300` downloads roughly 5 MB and measures what that feels like.
               The screenful stays a screenful regardless -- it answers the
               other question, which is how long you wait before the grid
               appears at all.

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
    """The thumbnails on one album page, and the albums below it.

    `--compressed` because searching a large tree fetches dozens of these and
    an album page of two hundred photographs is a few hundred kilobytes of
    HTML. Apache compresses it about thirtyfold, and curl does not ask unless
    told -- so the search was pulling megabytes down the very link it is about
    to measure. Only for HTML: the images must be fetched exactly as a browser
    would, and AVIF does not compress again.
    """
    body = curl(["--compressed", "-b", s.jar, f"{s.base}/a/{album.strip('/')}/"]).stdout
    thumbs: list[str] = []
    for m in re.finditer(r'srcset="([^"]*)"', body):
        for cand in m.group(1).split(", "):
            u = cand.rsplit(" ", 1)[0]
            if "/i/512/" in u:
                thumbs.append(s.base + u)

    # Every sub-album card carries its recursive photo count -- "1,311 photos"
    # -- so the page says exactly where the big albums are and there is no need
    # to guess. Pair each link with the next count in document order; the
    # album's own total sits in the header, above the first card, so it is
    # never mistaken for a child's.
    children: list[tuple[str, int]] = []
    for m in re.finditer(r'href="/a/([^"]+)/"(.*?)(?=href="/a/|\Z)', body, re.S):
        n = re.search(r'([\d,]+)\s+photos?', m.group(2))
        children.append((m.group(1), int(n.group(1).replace(",", "")) if n else 0))
    return thumbs, children


def find_album(s: Session, album: str, want: int) -> tuple[str, list[str]]:
    """Pick an album with at least `want` photographs, or the largest there is.

    Every sub-album card states its recursive photo count, so the tree says
    where the big albums are: open whichever unexplored album claims the most,
    and repeat. A blind crawl tried forty-one albums and settled for 194 while
    a 1,311-photograph album sat two clicks from the root.
    """
    if album:
        thumbs, _ = album_thumbs(s, album)
        if not thumbs:
            sys.exit(f"no thumbnails on /a/{album}/ -- wrong album, "
                     "or its images are not generated yet")
        return album, thumbs

    # Walk straight down, always into the sub-album claiming the most
    # photographs. The counts are recursive, so the biggest child is the way to
    # the biggest album beneath it, and a tree of any depth is a handful of
    # fetches rather than a crawl.
    #
    # Best-first on the counts alone does not work, which is worth recording:
    # a year holding 2,513 photographs outranks every individual album, so it
    # opens all twenty-six years before the first real album. Guided descent
    # found a 1,311-photograph album where that had settled for 249.
    best: tuple[str, list[str]] = ("", [])
    siblings: list[tuple[int, str]] = []        # passed over, kept for later
    seen: set[str] = set()
    current: str | None = ""
    while current is not None and len(best[1]) < want and len(seen) <= 12:
        seen.add(current)
        thumbs, children = album_thumbs(s, current)
        if len(thumbs) > len(best[1]):
            best = (current, thumbs)
        children = sorted(((n, p) for p, n in children if p not in seen),
                          reverse=True)
        siblings.extend(children[1:])
        current = children[0][1] if children else None

    # Nothing big enough on that path: fall back to the ones passed over,
    # biggest first, until the budget runs out.
    while siblings and len(best[1]) < want and len(seen) <= 12:
        siblings.sort(reverse=True)
        _, current = siblings.pop(0)
        if current in seen:
            continue
        seen.add(current)
        thumbs, children = album_thumbs(s, current)
        if len(thumbs) > len(best[1]):
            best = (current, thumbs)
        siblings.extend((n, p) for p, n in children if p not in seen)

    if not best[1]:
        sys.exit("found no album with generated thumbnails; pass --album")
    if len(best[1]) < want:
        print(f"note: wanted {want} thumbnails, and the largest album found in "
              f"{len(seen)} tried has {len(best[1])}. Pass --album if you know "
              f"a bigger one.\n", file=sys.stderr)
    return best[0], best[1]


def row(label: str, value: str, note: str = "") -> None:
    print(f"  {label:<40} {value:>10}   {note}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("base", help="site root, e.g. https://photos.example.org")
    p.add_argument("--user", default=os.environ.get("USER", ""))
    p.add_argument("--album", default="", help="album to pull thumbnails from")
    p.add_argument("--heavy", action="store_true",
                   help="also measure sustained throughput and originals")
    p.add_argument("--bulk", type=int, default=BULK, metavar="N",
                   help=f"thumbnails for the sustained test (default {BULK})")
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

    # Look for an album big enough for the largest test that will actually run.
    # Searching for a screenful and then asking --bulk for 300 finds a
    # 132-photograph album and quietly measures less than was asked for.
    want = max(args.bulk, GOOD_ENOUGH) if args.heavy else GOOD_ENOUGH
    album, found = find_album(s, args.album, want)
    thumbs = found[:SCREENFUL]
    relpath = thumbs[0].split("/i/512/", 1)[1].split("?")[0]

    where = f"/a/{album}/" if album else "/a/ (the top album)"
    # Only the album and its size. Describing the sample here was wrong twice
    # over: it read as though a 24-photograph album had been chosen, and with
    # --heavy there are two different samples anyway. Each row below says how
    # many it used, which is where that belongs.
    print(f"\n{s.base}  {where} -- {len(found)} thumbnails\n")

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
    # The count, not only the percentage. With a sample this size one unlucky
    # request is already 1.4%, so a percentage alone invites reading noise as a
    # finding -- which a threshold of "above 1%" did, on this very server.
    # What actually indicates a bug is a large share, or stalls that land on a
    # clock: the X-Sendfile hand-off stalled a third of all requests at almost
    # exactly one-second intervals, and that regularity was the giveaway.
    row(f"  ... stalled (>{STALL_MS} ms)",
        f"{over} of {len(seq)}",
        "a few under load is normal; a third of them is a bug")

    # ---- throughput: what a person actually waits for.
    runs = [s.parallel(thumbs, SCREENFUL) for _ in range(REPEATS)]
    best = sorted(runs, key=lambda r: r[0])[len(runs) // 2]
    secs, nbytes = best
    # The byte count, not just the time: a screenful of 2003 snapshots and one
    # of 2016 photographs are not the same number of kilobytes, so the seconds
    # are only comparable between runs over the same album. Printing both makes
    # a mismatched comparison obvious instead of silently misleading.
    row(f"screenful ({len(thumbs)} thumbnails, {nbytes / 1024:.0f} KB)",
        f"{secs:.2f} s", f"cold connection, {nbytes / 1024 / secs:.0f} KB/s")

    # And the same screenful on a connection that is already open, which is
    # what a browser actually has: it fetched the HTML over it a moment ago.
    #
    # Every figure above pays TCP slow start, because each curl run is a new
    # process and so a new connection. Over a 100 ms link that is several round
    # trips of ramp before the window is wide enough to matter -- which made
    # this tool report roughly twice the wait a person experiences, and sent me
    # hunting a throughput problem the browser barely felt.
    #
    # Estimated by difference: time one screenful, then time two screenfuls of
    # *different* photographs in one connection. Everything before the second
    # screenful is common to both, so subtracting leaves the second one alone,
    # warm. Different photographs, because repeating the first would measure
    # the server's page cache instead.
    second = found[len(thumbs):len(thumbs) * 2]
    if len(second) == len(thumbs):
        pairs = [s.parallel(thumbs + second, SCREENFUL) for _ in range(REPEATS)]
        both = sorted(p[0] for p in pairs)[len(pairs) // 2]
        warm = both - secs
        warm_bytes = sorted(p[1] for p in pairs)[len(pairs) // 2] - nbytes
        if warm > 0:
            row("  ... on a warm connection", f"{warm:.2f} s",
                f"what a browser sees, {warm_bytes / 1024 / warm:.0f} KB/s")

    if args.heavy:
        # Scrolling a long album: many small files, the same route and the same
        # multiplexed connection as the screenful, but enough of them that the
        # figure is throughput rather than slow start.
        bulk = found[:args.bulk]
        if len(bulk) > SCREENFUL:
            secs, nbytes = s.parallel(bulk, SCREENFUL)
            row(f"scrolling it ({len(bulk)} thumbnails, {nbytes / 1024:.0f} KB)",
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
  A few stalls .......................... normal when the machine is busy.
                                          A large share of them is a bug, and
                                          they cluster on a clock when
                                          something periodic is to blame.
  Screenful slow, per-request fine ...... throughput. Compare against the
                                          originals below (--heavy): if those
                                          are slow too it is the uplink, and if
                                          they are fast it is the serving path.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
