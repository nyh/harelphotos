"""The scanner (DESIGN.md 8).

Phase 1  walk        os.scandir, upsert rows, detect what disappeared
Phase 2  header      content signature + EXIF + dimensions, in parallel
Phase 3  derive      generate every image tier, in parallel (the expensive part)
Phase 4  rollup      recursive counts, date spans, covers; prune stale files

The load-bearing idea is in phase 2: **mtime decides whether to look,
content_sig decides whether to work.** Timestamps get rewritten by ordinary
metadata tidying (``jhead -ft``), and letting that trigger a re-encode of the
whole collection would be a 22 core-hour mistake.
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import sqlite3
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from . import acl, album, derive, exif
from .config import Config
from .util import natkey

log = logging.getLogger("harelphotos.scan")

PHOTO_EXTENSIONS = {".jpg", ".jpeg"}
SIG_BYTES = 16
COMMIT_BATCH = 200

# How often to redraw the progress line. Tied to the clock, not to a count of
# items: deriving images runs at a few per second, so a count-based trigger
# either never fires on a small directory or scrolls uselessly on a big one.
PROGRESS_INTERVAL = 0.25


@dataclass
class ScanStats:
    dirs_seen: int = 0
    dirs_added: int = 0
    dirs_removed: int = 0
    photos_seen: int = 0
    photos_added: int = 0
    photos_removed: int = 0
    photos_checked: int = 0        # phase 2 read the header
    photos_unchanged: int = 0      # ...and the bytes turned out to be the same
    photos_changed: int = 0        # ...and they really had changed
    photos_failed: int = 0
    photos_derived: int = 0
    derive_failed: int = 0
    bytes_written: int = 0
    files_removed: int = 0
    repaired: int = 0
    skipped_names: list[str] = field(default_factory=list)
    config_errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.dirs_seen} directories",
            f"{self.photos_seen} photos",
        ]
        if self.dirs_added or self.photos_added:
            parts.append(f"+{self.dirs_added} dirs / +{self.photos_added} photos")
        if self.dirs_removed or self.photos_removed:
            parts.append(f"-{self.dirs_removed} dirs / -{self.photos_removed} photos")
        if self.photos_checked:
            parts.append(
                f"{self.photos_checked} headers read "
                f"({self.photos_unchanged} unchanged, {self.photos_changed} changed)"
            )
        if self.photos_derived:
            parts.append(
                f"{self.photos_derived} derived ({self.bytes_written / 1e9:.2f} GB)"
            )
        if self.repaired:
            parts.append(f"{self.repaired} with missing files repaired")
        if self.files_removed:
            parts.append(f"{self.files_removed} stale files removed")
        if self.photos_failed or self.derive_failed:
            parts.append(f"{self.photos_failed + self.derive_failed} FAILED")
        if self.skipped_names:
            parts.append(f"{len(self.skipped_names)} unusable names skipped")
        if self.config_errors:
            parts.append(f"{len(self.config_errors)} .album.toml problems")
        return ", ".join(parts)


def is_photo(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS


def is_excluded(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(name, pat) for pat in patterns)


def content_signature(head: bytes, tail: bytes, size: int) -> bytes:
    """A cheap fingerprint of the file's actual bytes.

    Deliberately *not* a full-file hash: reading 300 GB to answer "did this
    change?" would cost more than the re-encode it saves. Size plus both ends
    catches every realistic edit to a JPEG.
    """
    h = hashlib.blake2b(digest_size=SIG_BYTES)
    h.update(size.to_bytes(8, "little"))
    h.update(head)
    h.update(tail)
    return h.digest()


def read_photo_header(path: Path) -> tuple[bytes, bytes, int]:
    """One open, two reads: the bytes both the signature and EXIF need."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(exif.HEAD_BYTES)
        if size > exif.HEAD_BYTES + exif.TAIL_BYTES:
            f.seek(-exif.TAIL_BYTES, os.SEEK_END)
            tail = f.read(exif.TAIL_BYTES)
        else:
            tail = b""      # already covered by head
    return head, tail, size


def _phase2_worker(job: tuple[int, str]) -> dict:
    """Runs in a pool process: read one photo's header. Never raises."""
    photo_id, path_str = job
    path = Path(path_str)
    out: dict = {"id": photo_id}
    try:
        head, tail, size = read_photo_header(path)
    except OSError as e:
        out["error"] = f"cannot read: {e}"
        return out
    out["size"] = size
    out["sig"] = content_signature(head, tail, size)
    hdr = exif.read_header(head, path)
    if hdr.error:
        out["error"] = hdr.error
        return out
    out["width"] = hdr.width
    out["height"] = hdr.height
    out["taken"] = hdr.taken
    out["exif_json"] = hdr.exif_json
    return out


def find_missing_derivatives(
    cfg: Config, conn: sqlite3.Connection, repair: bool = False
) -> list[str]:
    """Photos whose recorded tiers are not actually on disk.

    The database can legitimately disagree with the filesystem: part of the
    derived tree deleted to reclaim space, an interrupted copy, a disk that
    filled mid-write. Because `deriv_key` still matches, an ordinary rescan
    would skip those photos forever, so the mismatch has to be looked for
    deliberately — and `--repair` clears the key so the next pass rebuilds them.

    Read-only unless `repair`, so `check --verify-files` can call it on a
    read-only connection.
    """
    missing: list[str] = []
    rows = conn.execute(
        "SELECT p.id, p.deriv_tiers, d.path, p.name FROM photos p "
        "JOIN dirs d ON d.id = p.dir_id WHERE p.deriv_key IS NOT NULL"
    ).fetchall()
    for r in rows:
        try:
            tiers = json.loads(r["deriv_tiers"] or "[]")
        except ValueError:
            tiers = []
        relpath = f"{r['path']}/{r['name']}" if r["path"] else r["name"]
        if any(not derive.derived_path(cfg, t, relpath).exists() for t in tiers):
            missing.append(relpath)
            if repair:
                conn.execute("UPDATE photos SET deriv_key = NULL WHERE id = ?", (r["id"],))
    if repair and missing:
        conn.commit()
    return missing


def _phase3_worker(job: tuple[int, str, str, "Config"]) -> dict:
    """Runs in a pool process: derive every tier for one photo."""
    photo_id, path_str, relpath, cfg = job
    res = derive.derive(Path(path_str), relpath, cfg)
    return {
        "id": photo_id,
        "tiers": res.tiers,
        "colour": res.colour,
        "bytes": res.bytes_written,
        "error": res.error,
    }


class Scanner:
    def __init__(self, cfg: Config, conn: sqlite3.Connection) -> None:
        self.cfg = cfg
        self.conn = conn
        self.stats = ScanStats()
        self.generation = self._next_generation()

    def _next_generation(self) -> int:
        """A strictly increasing counter, persisted in the index.

        Deliberately not a timestamp: two scans in the same second would then
        share a generation, every row would still look "seen", and the prune
        would silently delete nothing.
        """
        from . import db as db_mod

        prev = db_mod.get_meta(self.conn, "scan_generation")
        try:
            gen = int(prev) + 1
        except (TypeError, ValueError):
            gen = 1
        db_mod.set_meta(self.conn, "scan_generation", str(gen))
        self.conn.commit()
        return gen

    # ---------------------------------------------------------------- phase 1

    def walk(self, subpath: str = "") -> None:
        """Depth-first walk, upserting rows and flagging what changed."""
        root = self.cfg.photo_root
        start = root / subpath if subpath else root
        if not start.is_dir():
            raise NotADirectoryError(f"not a directory: {start}")

        parent_chain: acl.Chain = ()
        parent_id = None
        if subpath:
            # Rescanning a subtree: inherit the chain its ancestors imposed.
            row = self.conn.execute(
                "SELECT id, acl_chain FROM dirs WHERE path = ?", (subpath,)
            ).fetchone()
            if row:
                parent_chain = acl.loads(row["acl_chain"])
        self._walk_dir(start, subpath, parent_id, parent_chain)

    def _walk_dir(
        self, abspath: Path, relpath: str, parent_id: int | None, parent_chain: acl.Chain
    ) -> int:
        cfg = album.load(abspath)
        if cfg.errors:
            self.stats.config_errors.append(f"{relpath or '.'}/{album.ALBUM_FILE}: {cfg.error_text}")

        chain = acl.extend_chain(parent_chain, cfg.allow, cfg.allow_replace)
        name = abspath.name if relpath else ""
        dir_id, added = self._upsert_dir(relpath, name, parent_id, cfg, chain, abspath)
        self.stats.dirs_seen += 1
        if added:
            self.stats.dirs_added += 1

        try:
            entries = list(os.scandir(abspath))
        except OSError as e:
            log.warning("cannot list %s: %s", abspath, e)
            return dir_id

        n_photos = n_subdirs = 0
        for entry in sorted(entries, key=lambda e: e.name):
            try:
                entry_name = entry.name
            except (UnicodeDecodeError, ValueError):
                self.stats.skipped_names.append(str(abspath))
                continue
            # DESIGN.md 19: filenames are assumed UTF-8. A surrogate-escaped
            # name is skipped and reported rather than crashing the run.
            if _has_surrogates(entry_name):
                self.stats.skipped_names.append(os.path.join(relpath, entry_name))
                continue
            if is_excluded(entry_name, self.cfg.scan.exclude):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_rel = f"{relpath}/{entry_name}" if relpath else entry_name
                    self._walk_dir(Path(entry.path), child_rel, dir_id, chain)
                    n_subdirs += 1
                elif entry.is_file(follow_symlinks=False) and is_photo(entry_name):
                    if self._upsert_photo(dir_id, entry):
                        self.stats.photos_added += 1
                    self.stats.photos_seen += 1
                    n_photos += 1
            except OSError as e:
                log.warning("cannot stat %s: %s", entry.path, e)

        self.conn.execute(
            "UPDATE dirs SET n_photos = ?, n_subdirs = ? WHERE id = ?",
            (n_photos, n_subdirs, dir_id),
        )
        return dir_id

    def _upsert_dir(
        self,
        relpath: str,
        name: str,
        parent_id: int | None,
        cfg: album.AlbumConfig,
        chain: acl.Chain,
        abspath: Path,
    ) -> tuple[int, bool]:
        try:
            st = (abspath / album.ALBUM_FILE).stat()
            cfg_mtime, cfg_size = st.st_mtime_ns, st.st_size
        except OSError:
            cfg_mtime, cfg_size = None, None

        sort = None if cfg.sort is None else ("-" + cfg.sort if cfg.reverse_sort else cfg.sort)
        dirsort = (
            None
            if cfg.dirsort is None
            else ("-" + cfg.dirsort if cfg.reverse_dirsort else cfg.dirsort)
        )
        values = {
            "path": relpath,
            "name": name,
            "title": cfg.title,
            "description": cfg.description,
            "sort": sort,
            "dirsort": dirsort,
            "order_json": json.dumps(list(cfg.order)) if cfg.order else None,
            "sort_key": cfg.sort_key,
            "natkey": natkey(cfg.sort_key or name),
            "group_by": cfg.group_by,
            "cover_spec": cfg.cover,
            "location": cfg.location,
            "hidden": int(cfg.hidden),
            "acl_chain": acl.dumps(chain),
            "cfg_mtime": cfg_mtime,
            "cfg_size": cfg_size,
            "cfg_error": cfg.error_text,
            "parent_id": parent_id,
            "seen": self.generation,
        }
        row = self.conn.execute("SELECT id FROM dirs WHERE path = ?", (relpath,)).fetchone()
        if row is None:
            cols = ", ".join(values)
            marks = ", ".join("?" * len(values))
            cur = self.conn.execute(
                f"INSERT INTO dirs ({cols}) VALUES ({marks})", list(values.values())
            )
            return int(cur.lastrowid), True
        assigns = ", ".join(f"{k} = ?" for k in values)
        self.conn.execute(
            f"UPDATE dirs SET {assigns} WHERE id = ?", [*values.values(), row["id"]]
        )
        return int(row["id"]), False

    def _upsert_photo(self, dir_id: int, entry: os.DirEntry) -> bool:
        st = entry.stat(follow_symlinks=False)
        size, mtime_ns = st.st_size, st.st_mtime_ns
        row = self.conn.execute(
            "SELECT id, size, mtime_ns FROM photos WHERE dir_id = ? AND name = ?",
            (dir_id, entry.name),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO photos (dir_id, name, size, mtime_ns, seen) VALUES (?,?,?,?,?)",
                (dir_id, entry.name, size, mtime_ns, self.generation),
            )
            return True
        # Only (size, mtime) decides whether phase 2 should LOOK, and that is
        # recorded in hdr_stale. content_sig is deliberately left alone: it is
        # the value phase 2 compares against to decide whether anything
        # actually changed, so clearing it here would destroy the evidence.
        if row["size"] != size or row["mtime_ns"] != mtime_ns:
            self.conn.execute(
                "UPDATE photos SET size = ?, mtime_ns = ?, seen = ?, hdr_stale = 1 "
                "WHERE id = ?",
                (size, mtime_ns, self.generation, row["id"]),
            )
        else:
            self.conn.execute(
                "UPDATE photos SET seen = ? WHERE id = ?", (self.generation, row["id"])
            )
        return False

    def prune(self, subpath: str = "") -> list[str]:
        """Delete rows for vanished files/directories; return their paths."""
        # Scope this exactly like the DELETE below. Collecting orphans across
        # the whole tree while deleting only within the subtree would have
        # `scan --dir X` delete every *other* directory's derivative files
        # while leaving their rows claiming the files still exist.
        if subpath:
            orphan_rows = self.conn.execute(
                "SELECT d.path, p.name FROM photos p JOIN dirs d ON d.id = p.dir_id "
                "WHERE p.seen != ? AND (d.path = ? OR d.path LIKE ?)",
                (self.generation, subpath, f"{subpath}/%"),
            )
        else:
            orphan_rows = self.conn.execute(
                "SELECT d.path, p.name FROM photos p JOIN dirs d ON d.id = p.dir_id "
                "WHERE p.seen != ?",
                (self.generation,),
            )
        orphans = [
            (f"{r['path']}/{r['name']}" if r["path"] else r["name"]) for r in orphan_rows
        ]
        if subpath:
            like = f"{subpath}/%"
            dirs = self.conn.execute(
                "SELECT count(*) AS n FROM dirs WHERE seen != ? AND (path = ? OR path LIKE ?)",
                (self.generation, subpath, like),
            ).fetchone()["n"]
            photos = self.conn.execute(
                "SELECT count(*) AS n FROM photos p JOIN dirs d ON d.id = p.dir_id "
                "WHERE p.seen != ? AND (d.path = ? OR d.path LIKE ?)",
                (self.generation, subpath, like),
            ).fetchone()["n"]
            self.conn.execute(
                "DELETE FROM photos WHERE seen != ? AND dir_id IN "
                "(SELECT id FROM dirs WHERE path = ? OR path LIKE ?)",
                (self.generation, subpath, like),
            )
            self.conn.execute(
                "DELETE FROM dirs WHERE seen != ? AND (path = ? OR path LIKE ?)",
                (self.generation, subpath, like),
            )
        else:
            dirs = self.conn.execute(
                "SELECT count(*) AS n FROM dirs WHERE seen != ?", (self.generation,)
            ).fetchone()["n"]
            photos = self.conn.execute(
                "SELECT count(*) AS n FROM photos WHERE seen != ?", (self.generation,)
            ).fetchone()["n"]
            self.conn.execute("DELETE FROM photos WHERE seen != ?", (self.generation,))
            self.conn.execute("DELETE FROM dirs WHERE seen != ?", (self.generation,))
        self.stats.dirs_removed = dirs
        self.stats.photos_removed = photos
        return orphans

    # ---------------------------------------------------------------- phase 2

    def pending_headers(self, limit: int | None = None) -> list[tuple[int, str]]:
        sql = (
            "SELECT p.id, d.path, p.name FROM photos p JOIN dirs d ON d.id = p.dir_id "
            "WHERE p.hdr_stale = 1 ORDER BY d.path, p.name"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        root = self.cfg.photo_root
        return [
            (r["id"], str(root / r["path"] / r["name"] if r["path"] else root / r["name"]))
            for r in self.conn.execute(sql)
        ]

    def read_headers(self, jobs: int | None = None, limit: int | None = None,
                     progress=None) -> None:
        pending = self.pending_headers(limit)
        if not pending:
            return
        jobs = jobs or self.cfg.scan.effective_jobs
        done = 0
        started = time.monotonic()
        last_shown = 0.0

        def apply(res: dict) -> None:
            nonlocal done, last_shown
            done += 1
            self.stats.photos_checked += 1
            if "error" in res:
                self.stats.photos_failed += 1
                # Leave hdr_stale set so a later run retries: a transient read
                # error on a network mount should not need --full to recover.
                self.conn.execute(
                    "UPDATE photos SET deriv_error = ? WHERE id = ?", (res["error"], res["id"])
                )
                return
            prev = self.conn.execute(
                "SELECT content_sig, deriv_key FROM photos WHERE id = ?", (res["id"],)
            ).fetchone()
            # Unchanged bytes: keep deriv_key so nothing gets re-encoded.
            unchanged = prev is not None and prev["content_sig"] == res["sig"]
            if unchanged:
                self.stats.photos_unchanged += 1
            else:
                self.stats.photos_changed += 1
            self.conn.execute(
                "UPDATE photos SET content_sig = ?, width = ?, height = ?, taken = ?, "
                "exif_json = ?, deriv_error = NULL, hdr_stale = 0 WHERE id = ?",
                (
                    res["sig"], res.get("width"), res.get("height"),
                    res.get("taken"), res.get("exif_json"), res["id"],
                ),
            )
            if done % COMMIT_BATCH == 0:
                self.conn.commit()
            now = time.monotonic()
            if progress and now - last_shown >= PROGRESS_INTERVAL:
                last_shown = now
                progress(done, len(pending), now - started)

        if jobs > 1 and len(pending) > 1:
            with multiprocessing.Pool(jobs, initializer=_nice, initargs=(self.cfg.scan.nice,)) as p:
                for res in p.imap_unordered(_phase2_worker, pending, chunksize=16):
                    apply(res)
        else:
            for job in pending:
                apply(_phase2_worker(job))
        self.conn.commit()
        if progress:
            progress(done, len(pending), time.monotonic() - started)

    # ---------------------------------------------------------------- phase 3

    def pending_derives(self, limit: int | None = None) -> list[tuple[int, str, str, Config]]:
        """Photos whose derivatives do not match the current recipe."""
        rows = self.conn.execute(
            "SELECT p.id, p.content_sig, p.deriv_key, d.path, p.name "
            "FROM photos p JOIN dirs d ON d.id = p.dir_id "
            "WHERE p.content_sig IS NOT NULL ORDER BY d.path, p.name"
        ).fetchall()
        jobs = []
        for r in rows:
            want = derive.deriv_key(r["content_sig"], self.cfg)
            if r["deriv_key"] == want:
                continue
            relpath = f"{r['path']}/{r['name']}" if r["path"] else r["name"]
            jobs.append((r["id"], str(self.cfg.photo_root / relpath), relpath, self.cfg))
            if limit and len(jobs) >= limit:
                break
        return jobs

    def derive_all(self, jobs_n: int | None = None, limit: int | None = None,
                   progress=None) -> None:
        pending = self.pending_derives(limit)
        if not pending:
            return
        jobs_n = jobs_n or self.cfg.scan.effective_jobs
        done = 0
        started = time.monotonic()
        last_shown = 0.0
        sigs = {
            r["id"]: r["content_sig"]
            for r in self.conn.execute("SELECT id, content_sig FROM photos")
        }

        def apply(res: dict) -> None:
            nonlocal done, last_shown
            done += 1
            if res["error"]:
                self.stats.derive_failed += 1
                self.conn.execute(
                    "UPDATE photos SET deriv_error = ? WHERE id = ?", (res["error"], res["id"])
                )
            else:
                self.stats.photos_derived += 1
                self.stats.bytes_written += res["bytes"]
                # deriv_key is stored only once every tier is on disk, so an
                # interrupted run simply resumes.
                self.conn.execute(
                    "UPDATE photos SET deriv_key = ?, deriv_tiers = ?, color = ?, "
                    "deriv_error = NULL WHERE id = ?",
                    (
                        derive.deriv_key(sigs.get(res["id"]), self.cfg),
                        json.dumps(res["tiers"]),
                        res["colour"],
                        res["id"],
                    ),
                )
            if done % COMMIT_BATCH == 0:
                self.conn.commit()
            now = time.monotonic()
            if progress and now - last_shown >= PROGRESS_INTERVAL:
                last_shown = now
                progress(done, len(pending), now - started)

        if jobs_n > 1 and len(pending) > 1:
            with multiprocessing.Pool(
                jobs_n, initializer=_nice, initargs=(self.cfg.scan.nice,)
            ) as p:
                for res in p.imap_unordered(_phase3_worker, pending, chunksize=4):
                    apply(res)
        else:
            for job in pending:
                apply(_phase3_worker(job))
        self.conn.commit()
        if progress:
            progress(done, len(pending), time.monotonic() - started)

    # ---------------------------------------------------------------- phase 4

    def prune_derivatives(self, orphans: list[str]) -> None:
        """Delete derivative files whose original is gone."""
        for relpath in orphans:
            self.stats.files_removed += derive.remove_derivatives(self.cfg, relpath)
        derive.prune_empty_dirs(self.cfg.derived_root)

    def rollup(self) -> None:
        """Recursive counts, date spans and cover photos, computed bottom-up.

        Deepest directories first, so each parent can simply add up children
        that are already final.
        """
        rows = self.conn.execute("SELECT id, path FROM dirs").fetchall()
        by_depth = sorted(rows, key=lambda r: r["path"].count("/") if r["path"] else -1,
                          reverse=True)
        for row in by_depth:
            did = row["id"]
            own = self.conn.execute(
                "SELECT count(*) AS n, min(taken) AS lo, max(taken) AS hi "
                "FROM photos WHERE dir_id = ? AND hidden = 0",
                (did,),
            ).fetchone()
            kids = self.conn.execute(
                "SELECT coalesce(sum(n_photos_rec), 0) AS n, min(date_min) AS lo, "
                "max(date_max) AS hi FROM dirs WHERE parent_id = ?",
                (did,),
            ).fetchone()
            lo = _min_opt(own["lo"], kids["lo"])
            hi = _max_opt(own["hi"], kids["hi"])
            self.conn.execute(
                "UPDATE dirs SET n_photos_rec = ?, date_min = ?, date_max = ?, cover_photo = ? "
                "WHERE id = ?",
                (own["n"] + kids["n"], lo, hi, self._resolve_cover(did), did),
            )
        self.conn.commit()

    def _resolve_cover(self, dir_id: int) -> int | None:
        """explicit cover -> first own photo -> first subdirectory's cover.

        Deterministic at every step, so covers do not shuffle between scans.
        The explicit spec was stored during phase 1: re-reading .album.toml
        here would add a filesystem round trip per directory, which is free
        locally and distinctly not free over sshfs.
        """
        row = self.conn.execute(
            "SELECT path, cover_spec FROM dirs WHERE id = ?", (dir_id,)
        ).fetchone()
        spec = row["cover_spec"]
        if spec and not spec.startswith("auto"):
            path, name = _split_cover(row["path"], spec)
            target = self.conn.execute(
                "SELECT p.id FROM photos p JOIN dirs d ON d.id = p.dir_id "
                "WHERE d.path = ? AND p.name = ?",
                (path, name),
            ).fetchone()
            if target:
                return int(target["id"])
            log.warning("%s: cover %r does not exist", row["path"] or ".", spec)
        own = self.conn.execute(
            "SELECT id FROM photos WHERE dir_id = ? AND hidden = 0 ORDER BY name LIMIT 1",
            (dir_id,),
        ).fetchone()
        if own:
            return int(own["id"])
        kid = self.conn.execute(
            "SELECT cover_photo FROM dirs WHERE parent_id = ? AND cover_photo IS NOT NULL "
            "AND hidden = 0 ORDER BY natkey LIMIT 1",
            (dir_id,),
        ).fetchone()
        return int(kid["cover_photo"]) if kid else None


def _split_cover(dir_path: str, spec: str) -> tuple[str, str]:
    """'kids/IMG_9.jpg' relative to '2019' -> ('2019/kids', 'IMG_9.jpg')."""
    sub, _, name = spec.rpartition("/")
    if not sub:
        return dir_path, name
    return (f"{dir_path}/{sub}" if dir_path else sub), name


def _min_opt(a, b):
    vals = [v for v in (a, b) if v is not None]
    return min(vals) if vals else None


def _max_opt(a, b):
    vals = [v for v in (a, b) if v is not None]
    return max(vals) if vals else None


def _has_surrogates(s: str) -> bool:
    return any("\ud800" <= ch <= "\udfff" for ch in s)


def _nice(level: int) -> None:
    try:
        os.nice(level)
    except OSError:
        pass


def scan(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    subpath: str = "",
    jobs: int | None = None,
    limit: int | None = None,
    full: bool = False,
    repair: bool = False,
    headers_only: bool = False,
    progress=None,
    derive_progress=None,
) -> ScanStats:
    """Run phases 1-4."""
    s = Scanner(cfg, conn)
    if full:
        # Force every header to be re-read, e.g. to pick up a new EXIF field.
        conn.execute("UPDATE photos SET hdr_stale = 1")
        conn.execute("UPDATE photos SET deriv_key = NULL")
    if repair:
        s.stats.repaired = len(find_missing_derivatives(cfg, conn, repair=True))
    s.walk(subpath)
    conn.commit()
    orphans = s.prune(subpath)
    conn.commit()
    s.read_headers(jobs=jobs, limit=limit, progress=progress)
    if not headers_only:
        s.derive_all(jobs_n=jobs, limit=limit, progress=derive_progress or progress)
        s.prune_derivatives(orphans)
    s.rollup()
    return s.stats
