"""Cross-thread and cross-process locks (POSIX flock). Parallel tasks share state files and one git repository."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:          # non-POSIX: threads are still protected, processes are not
    fcntl = None            # type: ignore[assignment]

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Re-entrant within a thread; exclusive across threads and processes."""
    key = str(path)
    with _GUARD:
        tl = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with tl:
        if fcntl is None:
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
            fcntl.flock(fh, fcntl.LOCK_EX)
            depth[key] = 1
            try:
                yield
            finally:
                depth[key] = 0
                fcntl.flock(fh, fcntl.LOCK_UN)


_local = threading.local()
