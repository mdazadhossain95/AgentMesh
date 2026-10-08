"""Live progress for a running worker: who, which model, elapsed, rough ETA."""
from __future__ import annotations

import threading
import time
from typing import Callable


def typical_seconds(worker: str, model: str | None, rows: list[dict]) -> float | None:
    """Benchmark average for (worker, model); falls back to the worker's mean. Rough: benchmark tasks are small."""
    exact = [r["avg_seconds"] for r in rows if r.get("worker") == worker and r.get("model") == model
             and r.get("avg_seconds")]
    if exact:
        return float(exact[0])
    same = [r["avg_seconds"] for r in rows if r.get("worker") == worker and r.get("avg_seconds")]
    return sum(same) / len(same) if same else None


def fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


class Heartbeat:
    """Context manager: emits a status line every `interval` seconds while a worker runs."""

    def __init__(self, emit: Callable[[str], None], label: str, timeout: int,
                 typical: float | None = None, interval: float = 15.0):
        self.emit, self.label, self.timeout, self.typical, self.interval = emit, label, timeout, typical, interval
        self._stop = threading.Event()
        self._t0 = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start_line(self) -> str:
        eta = f", small-task baseline ~{fmt(self.typical)}" if self.typical else ""
        return f"{self.label} started (timeout {fmt(self.timeout)}{eta})"

    def status_line(self, elapsed: float) -> str:
        parts = [f"{self.label} running {fmt(elapsed)}"]
        if self.typical:
            left = self.typical - elapsed
            parts.append(f"~{fmt(left)} left" if left > 0 else "over the small-task baseline")
        parts.append(f"timeout in {fmt(max(0, self.timeout - elapsed))}")
        return ", ".join(parts)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.emit(self.status_line(time.monotonic() - self._t0))

    def __enter__(self) -> "Heartbeat":
        self._t0 = time.monotonic()
        self.emit(self.start_line())
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self.emit(f"{self.label} finished in {fmt(time.monotonic() - self._t0)}")
