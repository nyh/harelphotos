"""``gc`` and ``stats`` (DESIGN.md 15).

The derived tree is a cache, and caches accumulate rubbish: a scan killed
halfway leaves files whose database row was never written, and retuning the
size ladder leaves whole tier directories nobody will ask for again.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import derive
from .config import Config


@dataclass
class GcResult:
    orphan_files: list[str] = field(default_factory=list)
    stale_tiers: list[str] = field(default_factory=list)
    bytes_freed: int = 0
    dirs_removed: int = 0


def collect(cfg: Config, conn: sqlite3.Connection, *, deep: bool = False,
            dry_run: bool = False) -> GcResult:
    """Remove derivatives with no matching photo, and unconfigured tiers."""
    r = GcResult()
    if not cfg.derived_root.is_dir():
        return r

    configured = {str(t) for t in cfg.sizes.tiers}
    for entry in sorted(cfg.derived_root.iterdir()):
        if entry.is_dir() and entry.name.isdigit() and entry.name not in configured:
            r.stale_tiers.append(entry.name)
            r.bytes_freed += _tree_size(entry)
            if not dry_run:
                _rmtree(entry)

    if deep:
        # Every derivative should correspond to a row in `photos`. Walking the
        # whole tree is the only way to catch files orphaned by a crash, since
        # by definition the database never learned about them.
        known = {
            (f"{row['path']}/{row['name']}" if row["path"] else row["name"])
            for row in conn.execute(
                "SELECT d.path, p.name FROM photos p JOIN dirs d ON d.id = p.dir_id"
            )
        }
        for tier_dir in derive._tier_dirs(cfg):
            for dirpath, _, filenames in os.walk(tier_dir):
                for fn in filenames:
                    if fn.startswith(".") or ".tmp." in fn:
                        # A leftover temporary file from a killed encode.
                        full = Path(dirpath) / fn
                        r.orphan_files.append(str(full))
                        r.bytes_freed += _size(full)
                        if not dry_run:
                            full.unlink(missing_ok=True)
                        continue
                    full = Path(dirpath) / fn
                    rel = full.relative_to(tier_dir)
                    original = str(rel).rsplit(".", 1)[0]   # strip .avif/.webp/.jpeg
                    if original not in known:
                        r.orphan_files.append(str(full))
                        r.bytes_freed += _size(full)
                        if not dry_run:
                            full.unlink(missing_ok=True)

    if not dry_run:
        r.dirs_removed = derive.prune_empty_dirs(cfg.derived_root)
    return r


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _tree_size(root: Path) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            total += _size(Path(dirpath) / fn)
    return total


def _rmtree(root: Path) -> None:
    import shutil

    shutil.rmtree(root, ignore_errors=True)


@dataclass
class Stats:
    dirs: int = 0
    photos: int = 0
    derived_photos: int = 0
    pending: int = 0
    failed: int = 0
    with_place: int = 0
    derived_bytes: int = 0
    derived_files: int = 0
    per_tier: list[tuple[str, int, int]] = field(default_factory=list)
    biggest: list[tuple[str, int]] = field(default_factory=list)


def gather(cfg: Config, conn: sqlite3.Connection) -> Stats:
    s = Stats()
    s.dirs = conn.execute("SELECT count(*) AS n FROM dirs").fetchone()["n"]
    s.photos = conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"]
    s.derived_photos = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE deriv_key IS NOT NULL"
    ).fetchone()["n"]
    s.pending = s.photos - s.derived_photos
    s.failed = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE deriv_error IS NOT NULL"
    ).fetchone()["n"]
    s.with_place = conn.execute(
        "SELECT count(*) AS n FROM photos WHERE place IS NOT NULL"
    ).fetchone()["n"]
    s.biggest = [
        (r["path"] or ".", r["n_photos"])
        for r in conn.execute(
            "SELECT path, n_photos FROM dirs WHERE n_photos > 0 "
            "ORDER BY n_photos DESC LIMIT 10"
        )
    ]
    if cfg.derived_root.is_dir():
        for tier_dir in sorted(derive._tier_dirs(cfg), key=lambda p: p.name):
            n = size = 0
            for dirpath, _, filenames in os.walk(tier_dir):
                for fn in filenames:
                    n += 1
                    size += _size(Path(dirpath) / fn)
            label = (
                tier_dir.name
                if tier_dir.parent == cfg.derived_root
                else f"{tier_dir.parent.name}/{tier_dir.name}"
            )
            s.per_tier.append((label, n, size))
            s.derived_files += n
            s.derived_bytes += size
    return s
