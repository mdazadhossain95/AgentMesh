"""Best-effort check for a newer AgentMesh release on GitHub. Never raises, never blocks noticeably."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from . import __version__
from .config import global_home

PYPROJECT_URL = "https://raw.githubusercontent.com/mdazadhossain95/AgentMesh/main/pyproject.toml"
TTL_SECONDS = 24 * 60 * 60
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def cache_path() -> Path:
    return global_home() / "update_check.json"


def _parse(v: str) -> tuple[int, ...]:
    out = []
    for p in v.split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def _fetch_latest(timeout: float = 1.5) -> str | None:
    try:
        with urllib.request.urlopen(PYPROJECT_URL, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def check(*, force: bool = False) -> str | None:
    """Return a newer version string if one exists on GitHub main, else None. Result cached ~24h."""
    p = cache_path()
    now = time.time()
    if not force and p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if now - data.get("checked_at", 0) < TTL_SECONDS:
                latest = data.get("latest")
                return latest if latest and _parse(latest) > _parse(__version__) else None
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    latest = _fetch_latest()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"checked_at": now, "latest": latest or __version__}), encoding="utf-8")
    except OSError:
        pass
    return latest if latest and _parse(latest) > _parse(__version__) else None
