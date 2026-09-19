#!/usr/bin/env python3
"""Set a WhatsApp photo's file date from the date in its name.

WhatsApp strips metadata from everything it sends. A photograph that arrives
that way has no EXIF at all -- not a stripped date field, no APP1 segment
whatsoever -- so there is nothing to recover from inside the file, and
harelphotos falls back to the file's own modification time. That is whenever
the file was last copied, which for a folder moved off a phone is one timestamp
for the lot: a holiday's worth of photographs dated the day you copied them,
clumped together at the end of a date-sorted album.

The date is not lost, though. WhatsApp puts it in the name:

    IMG-20260716-WA0011.jpg     16 July 2026, the 11th item WhatsApp saved
    VID-20260716-WA0012.mp4     videos are named the same way
    IMG-20260731-WA0122(1).jpg  a second download of the same item

So this sets the file's modification time from its name, and the next
`harelphotos scan` picks it up. Nothing is re-encoded: the scan notices the
changed timestamp, reads the file's header, finds the image bytes identical to
what it already has, and keeps every generated image exactly as it is.

WHAT TIME OF DAY. Midday, plus the WhatsApp counter in seconds.

A name carries a date and no time, so some hour has to be invented, and midday
is the one furthest from both midnights: a timestamp read in a timezone up to
eleven hours away still falls on the right day, where 23:59 would not.

The counter goes on top, as seconds, because WhatsApp numbers each day's items
in the order it saved them -- so a day's photographs keep that order instead of
piling onto one instant. Seconds and not something finer because the index
sorts by whole seconds, so anything smaller would not survive the trip.

The offset is capped at one hour, which keeps every result inside 12:00-12:59
and so keeps the eleven hours of slack in both directions. Real counters are
nowhere near it -- across one 96-file album they run from 6 to 264 -- and a day
with more than 3599 WhatsApp items would merely have its last few share a
timestamp and sort by name.

WHAT THIS DATE MEANS. It is when WhatsApp saved the file, not when the shutter
was pressed. For a photograph shared soon after it was taken, which is the
ordinary case, those are the same day. For one somebody forwards years later it
is the day it was forwarded. That is a guess, where EXIF would be a fact -- but
it is a far better guess than the day the files happened to be copied.

    python3 contrib/fixup_whatsapp_dates.py ~/pictures/2026/07/thailand
    python3 contrib/fixup_whatsapp_dates.py --apply ~/pictures/2026/07

Nothing is written without --apply. Run it, read what it proposes, then run it
again with --apply and scan.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# IMG-20260716-WA0011.jpg, and the same shape for videos. Anything may follow
# the counter -- a "(1)" from a second download is common -- so the suffix is
# not anchored, only the prefix, the date and the counter.
WHATSAPP = re.compile(r"^(?:IMG|VID)-(\d{4})(\d{2})(\d{2})-WA(\d+)", re.IGNORECASE)

MIDDAY = 12 * 3600

# See the note above on why the counter is capped rather than used whole.
MAX_OFFSET = 3599


def timestamp_for(name: str) -> float | None:
    """The file date a WhatsApp name implies, or None if it is not one."""
    m = WHATSAPP.match(name)
    if not m:
        return None
    year, month, day, counter = (int(g) for g in m.groups())
    try:
        midnight = datetime(year, month, day).timestamp()
    except ValueError:              # 20261332, and other impossible dates
        return None
    return midnight + MIDDAY + min(counter, MAX_OFFSET)


def walk(paths: list[str]):
    """Every file named on the command line, and every file under any
    directory named there."""
    for p in paths:
        path = Path(p)
        if path.is_dir():
            yield from sorted(f for f in path.rglob("*") if f.is_file())
        elif path.is_file():
            yield path
        else:
            print(f"not found: {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Set WhatsApp files' modification time from their name.",
        epilog="Without --apply nothing is written; it only says what it would do.",
    )
    ap.add_argument("paths", nargs="+", metavar="FILE_OR_DIR")
    ap.add_argument("--apply", action="store_true", help="actually change the files")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also list files it is leaving alone")
    args = ap.parse_args(argv)

    changed = already = skipped = 0
    for path in walk(args.paths):
        want = timestamp_for(path.name)
        if want is None:
            skipped += 1
            if args.verbose:
                print(f"  not a WhatsApp name, left alone: {path}")
            continue
        have = path.stat().st_mtime
        if abs(have - want) < 1:
            already += 1
            if args.verbose:
                print(f"  already dated: {path}")
            continue
        print(f"{path}\n    {_show(have)}  ->  {_show(want)}")
        if args.apply:
            # atime too: leaving it behind the mtime is untidy, and nothing
            # here depends on it.
            os.utime(path, (want, want))
        changed += 1

    what = "changed" if args.apply else "would change"
    print(f"\n{what} {changed}, already correct {already}, not WhatsApp names {skipped}")
    if changed and not args.apply:
        print("Nothing was written. Pass --apply to do it.")
    elif changed:
        print("Now run 'harelphotos scan' -- no image will be regenerated.")
    return 0


def _show(ts: float) -> str:
    return time.strftime("%a %d %b %Y %H:%M:%S", time.localtime(ts))


if __name__ == "__main__":
    sys.exit(main())
