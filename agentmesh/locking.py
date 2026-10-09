"""Cross-thread and cross-process locks (POSIX flock, Windows msvcrt.locking). Parallel tasks share state files and one git repository."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl
    msvcrt = None
except ImportError:          # Windows: byte-range lock on the first byte of the lock file
    fcntl = None            # type: ignore[assignment]
    try:
        import msvcrt
    except ImportError:      # neither: threads are still protected, processes are not
        msvcrt = None       # type: ignore[assignment]


def _lock(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh, fcntl.LOCK_EX)
    elif msvcrt is not None:
        fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)    # blocks ~10s per call, so loop
                return
            except OSError:
                continue


def _unlock(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh, fcntl.LOCK_UN)
    elif msvcrt is not None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Re-entrant within a thread; exclusive across threads and processes."""
    key = str(path)
    with _GUARD:
        tl = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with tl:
        if fcntl is None and msvcrt is None:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        depth = getattr(_local, "depth", None)
        if depth is None:
            depth = _local.depth = {}
        if depth.get(key):                 # already held by this thread
            depth[key] += 1
            try:
                yield
            finally:
                depth[key] -= 1
            return
        with path.open("a+") as fh:
            _lock(fh)
            depth[key] = 1
            try:
                yield
            finally:
                depth[key] = 0
                _unlock(fh)


_local = threading.local()
