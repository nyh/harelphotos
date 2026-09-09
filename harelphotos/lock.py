"""An exclusive lock around mutating operations (DESIGN.md 8).

Two concurrent scans would not corrupt SQLite, but they would duplicate work,
interleave scan generations, and let one process's prune delete files the
other just wrote. Cheaper to forbid than to reason about.
"""

from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path


class LockBusy(Exception):
    """Someone else holds the lock."""


class ScanLock:
    """A whole-run flock, held for the lifetime of the context manager."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> ScanLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            holder = ""
            try:
                holder = os.read(fd, 200).decode("utf-8", "replace").strip()
            except OSError:
                pass
            os.close(fd)
            if e.errno in (errno.EACCES, errno.EAGAIN):
                detail = f" (held by {holder})" if holder else ""
                raise LockBusy(
                    f"another harelphotos run is in progress{detail}. "
                    f"Wait for it, or use --force-unlock if it is dead."
                ) from e
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"pid {os.getpid()} started {time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                os.ftruncate(self._fd, 0)
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def break_lock(path: Path) -> bool:
    """Remove a stale lock file after a hard reset. Returns True if removed."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
