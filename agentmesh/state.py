"""Per-project runtime state: cooldowns and recent failures. Lives in .agentmesh/state (gitignored)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .locking import file_lock


class RuntimeState:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self.lock_path = path.with_suffix(".lock")
        self.data: dict[str, Any] = {"workers": {}}
        self.refresh()

    def refresh(self) -> None:
        """Re-read from disk: other tasks (threads or processes) may have recorded failures meanwhile."""
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

    def _w(self, worker: str) -> dict[str, Any]:
        return self.data.setdefault("workers", {}).setdefault(
            worker, {"uses": 0, "last_used": 0.0, "cooldown_until": 0.0, "last_error": None, "recent_failures": []})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".tmp{id(self)}")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)               # atomic: readers never see a half-written file

    def record_use(self, worker: str) -> None:
        with file_lock(self.lock_path):
            self.refresh()
            w = self._w(worker)
            w["uses"] += 1
            w["last_used"] = self.clock()
            self.save()

    def record_success(self, worker: str) -> None:
        with file_lock(self.lock_path):
            self.refresh()
            w = self._w(worker)
            w["cooldown_until"], w["last_error"] = 0.0, None
            self.save()

    def record_failure(self, worker: str, code: str, cooldown_seconds: int = 0) -> None:
        with file_lock(self.lock_path):
            self.refresh()
            w = self._w(worker)
            now = self.clock()
            w["last_error"] = code
            w["recent_failures"] = (w["recent_failures"] + [{"code": code, "at": now}])[-10:]
            if cooldown_seconds > 0:
                w["cooldown_until"] = now + cooldown_seconds
            self.save()

    def cooldown_remaining(self, worker: str) -> int:
        until = self.data.get("workers", {}).get(worker, {}).get("cooldown_until", 0.0)
        return max(0, int(until - self.clock()))

    def cooling(self) -> dict[str, int]:
        """Everything currently cooling down (workers and 'worker::model' keys) -> seconds left."""
        return {k: r for k in self.data.get("workers", {}) if (r := self.cooldown_remaining(k)) > 0}

    def last_error(self, key: str) -> str | None:
        return self.data.get("workers", {}).get(key, {}).get("last_error")

    def uses(self, worker: str) -> int:
        return int(self.data.get("workers", {}).get(worker, {}).get("uses", 0))

    def last_used(self, worker: str) -> float:
        return float(self.data.get("workers", {}).get(worker, {}).get("last_used", 0.0))

    def status(self, worker: str) -> str:
        """Runtime-observed status only. Remaining quota is never known, so never shown."""
        w = self.data.get("workers", {}).get(worker)
        if not w:
            return "OK"
        if self.cooldown_remaining(worker) > 0:
            return "COOLDOWN" if w["last_error"] not in ("QUOTA_EXCEEDED", "RATE_LIMITED") else w["last_error"]
        return "RECENT_FAILURE" if w.get("last_error") else "OK"

    def clear(self) -> None:
        self.data = {"workers": {}}
        if self.path.exists():
            self.path.unlink()
