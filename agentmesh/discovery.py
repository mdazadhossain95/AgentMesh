"""Machine-level discovery of coding CLIs. Free: only --version/--help are executed."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .config import global_home
from .models import AgentInfo, utcnow
from .registry import Registry

# CLIs we know exist in the wild but ship no adapter. Reported as "adapter required".
CANDIDATE_NAMES = ("aider", "goose", "crush", "cursor-agent", "amp", "droid", "cline", "kiro", "kiro-cli",
                   "auggie", "copilot", "plandex", "codebuff", "forge", "warp-agent", "jules")
_HEURISTIC = re.compile(r"^[a-z0-9]+-(code|coder)$")


@dataclass
class UnknownCli:
    name: str
    path: str


@dataclass
class DiscoveryReport:
    agents: list[AgentInfo] = field(default_factory=list)
    unknown: list[UnknownCli] = field(default_factory=list)
    generated_at: str = ""

    def by_name(self) -> dict[str, AgentInfo]:
        return {a.name: a for a in self.agents}

    def to_dict(self) -> dict:
        return {"generated_at": self.generated_at, "agents": [a.to_dict() for a in self.agents],
                "unknown": [u.__dict__ for u in self.unknown]}

    @classmethod
    def from_dict(cls, d: dict) -> "DiscoveryReport":
        return cls([AgentInfo.from_dict(a) for a in d.get("agents", [])],
                   [UnknownCli(**u) for u in d.get("unknown", [])], d.get("generated_at", ""))


def _is_exec(p: Path) -> bool:
    try:
        return p.is_file() and bool(p.stat().st_mode & stat.S_IXUSR)
    except OSError:
        return False


def scan_unknown(registry: Registry, path_env: str | None = None, extra: tuple[str, ...] = ()) -> list[UnknownCli]:
    known = registry.known_executables | {a.name for a in registry.adapters()}
    wanted = set(CANDIDATE_NAMES) | set(extra)
    seen: dict[str, str] = {}
    for d in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep):
        base = Path(d)
        if not d or not base.is_dir():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.name in known or p.name in seen:
                continue
            if (p.name in wanted or _HEURISTIC.match(p.name)) and _is_exec(p):
                seen[p.name] = str(p)
    return [UnknownCli(n, seen[n]) for n in sorted(seen)]


def discover(registry: Registry, path_env: str | None = None, extra_candidates: tuple[str, ...] = ()) -> DiscoveryReport:
    agents = [a.health_check(path_env) for a in registry.adapters()]
    return DiscoveryReport(agents, scan_unknown(registry, path_env, extra_candidates), utcnow())


def cache_path() -> Path:
    return global_home() / "discovery.json"


def save_cache(report: DiscoveryReport) -> None:
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def load_cache() -> DiscoveryReport | None:
    p = cache_path()
    if not p.is_file():
        return None
    try:
        return DiscoveryReport.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return None


def current(registry: Registry, *, refresh: bool = False, path_env: str | None = None) -> DiscoveryReport:
    """Cached report unless refresh/missing. Mock adapters are always re-probed (free, in-process)."""
    rep = None if refresh else load_cache()
    if rep is None:
        rep = discover(registry, path_env)
        save_cache(rep)
    by = rep.by_name()
    for a in registry.adapters():
        if a.name not in by or type(a).__name__ == "MockAdapter":
            info = a.health_check(path_env)
            rep.agents = [x for x in rep.agents if x.name != a.name] + [info]
    return rep
