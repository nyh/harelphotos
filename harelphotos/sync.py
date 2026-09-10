"""``harelphotos sync`` — copy the generated state to the serving machine.

The server is CPU-weak; encoding ~100k photos on it would take days. So the
scan runs where the CPU is, and the result — the generated images and the
index that names them — is copied over. Nothing here touches the photos
themselves.

Three things this has to get right, all of them about a site that is *serving*
while the copy runs:

* **Order.** Images first, index second. An index that arrives first names
  files that are not there yet, and the site shows broken thumbnails until
  rsync catches up. The other way round the extra images are simply unreferenced
  for a few minutes, which nobody can see.

* **A consistent index.** The index is a live WAL-mode SQLite database, so it
  is really three files, and copying the main one alone can produce a database
  that is torn or empty. ``VACUUM INTO`` writes a single consistent snapshot
  even while a scan is writing.

* **An atomic swap.** The snapshot lands beside the real index and is renamed
  over it. ``rename`` is atomic, and the web process opens the index per
  request, so requests either see the whole old index or the whole new one and
  never a half-written file.

Deleting is deliberately not part of this. A ``--delete`` here would race the
order above and remove images the far side's old index still points at; the
server can reclaim them itself afterwards with ``harelphotos gc``.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging
import shutil
import subprocess
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

log = logging.getLogger("harelphotos.sync")

# Filenames under state_dir, so both ends agree without a second config.
INDEX_NAME = "index.sqlite"
GEONAMES_NAME = "geonames.sqlite"


class SyncError(Exception):
    pass


@dataclass
class Destination:
    """``[user@]host:/path`` — the far side's *state* directory."""

    host: str
    path: str

    @classmethod
    def parse(cls, raw: str) -> Destination:
        # rsplit, not split: an IPv6 host in brackets or a user@ prefix both
        # contain colons, and the path is what follows the last one.
        if ":" not in raw:
            raise SyncError(
                f"{raw!r} is not a destination — expected [user@]host:/path, "
                f"for example nyh@harel.org.il:/var/lib/harelphotos"
            )
        host, _, path = raw.rpartition(":")
        if not host or not path:
            raise SyncError(f"{raw!r} is missing a host or a path")
        if not path.startswith("/"):
            raise SyncError(
                f"{path!r} must be an absolute path: a relative one lands in "
                f"the remote home directory, which is rarely what is meant"
            )
        return cls(host=host, path=path.rstrip("/"))

    def __str__(self) -> str:
        return f"{self.host}:{self.path}"


@dataclass
class Result:
    steps: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    def add(self, what: str, detail: str = "") -> None:
        self.steps.append((what, detail))


def _run(cmd: list[str], *, dry_run: bool) -> None:
    log.info("%s", " ".join(cmd))
    if dry_run:
        return
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as e:
        raise SyncError(f"{cmd[0]} is not installed: {e}") from e
    except subprocess.CalledProcessError as e:
        raise SyncError(f"{cmd[0]} failed with status {e.returncode}") from e


def snapshot_index(index_db: Path, dest: Path) -> None:
    """Write a consistent single-file copy of the index.

    ``VACUUM INTO`` rather than copying the file: the index is WAL-mode, so
    the bytes on disk are only half the story while anything is writing, and
    the copy would be torn. It also compacts, which makes the transfer smaller.
    """
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()


def _rsync(src: str, dst: str, *, dry_run: bool, extra: list[str] | None = None) -> None:
    if not shutil.which("rsync"):
        raise SyncError("rsync is not installed")
    cmd = [
        "rsync",
        "--archive",
        "--partial",       # a dropped connection resumes rather than restarts
        "--human-readable",
        "--info=stats1,progress2",
    ]
    cmd += extra or []
    if dry_run:
        cmd.append("--dry-run")
    cmd += [src, dst]
    _run(cmd, dry_run=False)   # rsync's own --dry-run, so it still reports


def run(
    cfg: Config,
    dest: Destination,
    *,
    dry_run: bool = False,
    geonames: bool = False,
    index_only: bool = False,
) -> Result:
    r = Result(dry_run=dry_run)

    if not cfg.index_db.exists():
        raise SyncError(f"{cfg.index_db} does not exist — run 'harelphotos scan' first")

    # 1. Images first. See the module docstring: the far side's old index keeps
    #    working throughout, because everything it names is still there.
    if not index_only:
        if not cfg.derived_root.is_dir():
            raise SyncError(f"{cfg.derived_root} does not exist — nothing to send")
        _rsync(
            f"{cfg.derived_root}/",
            f"{dest.host}:{dest.path}/derived/",
            dry_run=dry_run,
        )
        r.add("images", f"{cfg.derived_root} -> {dest}/derived/")

    if geonames:
        geo = cfg.state_dir / GEONAMES_NAME
        if not geo.is_file():
            raise SyncError(f"{geo} does not exist — run 'harelphotos init --geonames'")
        _rsync(str(geo), f"{dest.host}:{dest.path}/{GEONAMES_NAME}", dry_run=dry_run)
        r.add("place names", f"{geo.name} -> {dest}/")

    # 2. Then the index, via a consistent snapshot.
    with tempfile.TemporaryDirectory(prefix="harelphotos-sync-") as tmp:
        snap = Path(tmp) / INDEX_NAME
        if dry_run:
            r.add("index snapshot", f"would VACUUM INTO a copy of {cfg.index_db}")
        else:
            snapshot_index(cfg.index_db, snap)
            r.add("index snapshot", f"{snap.stat().st_size / 1e6:.1f} MB")

        incoming = f"{dest.path}/{INDEX_NAME}.incoming"
        if not dry_run:
            _rsync(str(snap), f"{dest.host}:{incoming}", dry_run=False)

        # 3. And swap it in atomically, so no request ever sees a partial file.
        _run(
            ["ssh", dest.host, "mv", "-f", incoming, f"{dest.path}/{INDEX_NAME}"],
            dry_run=dry_run,
        )
        r.add("index", f"renamed into place at {dest}/{INDEX_NAME}")

    # The stale -wal and -shm belong to the file that was just replaced. Left
    # alone, SQLite would try to recover them against the new database.
    _run(
        ["ssh", dest.host, "rm", "-f",
         f"{dest.path}/{INDEX_NAME}-wal", f"{dest.path}/{INDEX_NAME}-shm"],
        dry_run=dry_run,
    )
    r.add("stale WAL", "removed")

    return r
