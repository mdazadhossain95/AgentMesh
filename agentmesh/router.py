"""Worker selection. Role is chosen first (by workflow); this picks the engine for it."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import ProjectConfig
from .models import AgentInfo
from .registry import Registry
from .state import RuntimeState

STRATEGIES = ("balanced", "quality-first", "free-first", "preferred-order")


@dataclass
class Ranking:
    candidates: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)   # worker -> reason


class Router:
    def __init__(self, registry: Registry, infos: dict[str, AgentInfo], config: ProjectConfig, state: RuntimeState):
        self.registry, self.infos, self.config, self.state = registry, infos, config, state

    def _eligible(self, ranking: Ranking, exclude: set[str], explicit: set[str]) -> list[str]:
        allow_unverified = bool(self.config.get("routing.allow_unverified"))
        disabled = {self.registry.resolve(w) for w in self.config.get("routing.disabled", [])}
        out = []
        for name in self.registry.names():
            info = self.infos.get(name)
            if name in exclude:
                ranking.skipped[name] = "already tried"
            elif name in disabled:
                ranking.skipped[name] = "disabled in routing.disabled"
            elif info is None or not info.installed:
                ranking.skipped[name] = "not installed"
            elif info.state == "READY" or (allow_unverified and info.state == "UNVERIFIED"):
                if self.state.cooldown_remaining(name) > 0 and name not in explicit:
                    ranking.skipped[name] = f"{self.state.status(name)} ({self.state.cooldown_remaining(name)}s left)"
                else:
                    out.append(name)
            else:
                ranking.skipped[name] = info.state
        return out

    def rank(self, role: str, *, strategy: str | None = None, exclude: set[str] | None = None,
             preferred: str | None = None, avoid: set[str] | None = None) -> Ranking:
        self.state.refresh()
        strategy = strategy or self.config.get("routing.strategy", "balanced")
        exclude, avoid = set(exclude or ()), {self.registry.resolve(a) for a in (avoid or ())}
        res = lambda xs: [self.registry.resolve(x) for x in xs]
        role_pref = res(self.config.get(f"routing.roles.{role}", []) or [])
        explicit = set(role_pref) | ({self.registry.resolve(preferred)} if preferred else set())
        ranking = Ranking()
        eligible = self._eligible(ranking, exclude, explicit)

        order_pref = res(self.config.get("routing.preferred_order", []))
        quality = res(self.config.get("routing.quality_order", []))
        free = res(self.config.get("routing.free_workers", []))

        def position(seq: list[str]):
            return lambda n: seq.index(n) if n in seq else len(seq)

        base = sorted(eligible, key=position(order_pref + [n for n in self.registry.names() if n not in order_pref]))
        if strategy == "quality-first":
            ordered = sorted(base, key=position(quality))
        elif strategy == "free-first":
            ordered = sorted(base, key=lambda n: (0 if n in free else 1, base.index(n)))
        elif strategy == "balanced":
            ordered = sorted(base, key=lambda n: (self.state.uses(n), self.state.last_used(n), base.index(n)))
        else:  # preferred-order
            ordered = base

        # Explicit wishes always lead: role routing list, then the caller's preferred worker.
        lead = [n for n in role_pref if n in eligible]
        if preferred and self.registry.resolve(preferred) in eligible:
            lead = [self.registry.resolve(preferred)] + [n for n in lead if n != self.registry.resolve(preferred)]
        ordered = lead + [n for n in ordered if n not in lead]
        # Soft preference: keep workers that did the implementation away from review of it.
        ordered = [n for n in ordered if n not in avoid] + [n for n in ordered if n in avoid]
        ranking.candidates = ordered
        return ranking
