"""Risk tiers (low/normal/high) from task text and touched paths. Deterministic; no model calls."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import scope
from .roles import CONTRACT_GLOBS
from .workflow import REVIEW_ROLES, SIGNALS, detect_signals

TIERS = ("low", "normal", "high")
HIGH_TEXT = ("security", "financial", "api", "backend")
HIGH_WORDS = ("migration", "database", "schema", "drop table", "delete data", "production", "deploy")
HIGH_PATHS = ["**/auth/**", "**/*auth*", "**/api/**", "**/migrations/**", "**/*.sql", "**/payment*/**", "**/billing/**",
              "**/security/**", "**/db/**", "**/database/**", "**/backend/**", "**/server/**", *CONTRACT_GLOBS]
LOW_PATHS = ["**/*.md", "**/*.txt", "**/*.rst", "docs/**", "LICENSE", "**/LICENSE", ".gitignore", ".editorconfig"]


@dataclass
class Risk:
    tier: str = "normal"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"tier": self.tier, "reasons": self.reasons}


def _text_tier(text: str, vocab: dict[str, list[str]]) -> tuple[str, list[str]]:
    signals = set(detect_signals(text, vocab))
    low = f" {text.lower()} "
    hits = sorted(signals & set(HIGH_TEXT)) + [w for w in HIGH_WORDS if w in low]
    if hits:
        return "high", [f"text mentions {', '.join(hits)}"]
    if "trivial" in signals and "multi_area" not in signals:
        return "low", ["text looks like a trivial change"]
    return "normal", []


def _path_tier(files: list[str], high: list[str], low: list[str]) -> tuple[str | None, list[str]]:
    if not files:
        return None, []
    hot = [f for f in files if scope.matches(f, high)]
    if hot:
        return "high", [f"touches {', '.join(hot[:3])}" + (" ..." if len(hot) > 3 else "")]
    if all(scope.matches(f, low) for f in files):
        return "low", ["only docs/text files touched"]
    return "normal", []


def assess(text: str, files: list[str] | None = None, *, override: str | None = None,
           vocab: dict[str, list[str]] | None = None, high_paths: list[str] | None = None,
           low_paths: list[str] | None = None) -> Risk:
    """--risk override wins; else high if text or paths say so, low only if nothing argues for more."""
    if override:
        if override not in TIERS:
            raise ValueError(f"risk must be one of {TIERS}")
        return Risk(override, ["set by --risk"])
    t_tier, t_why = _text_tier(text, vocab or SIGNALS)
    p_tier, p_why = _path_tier(list(files or []), HIGH_PATHS + list(high_paths or []), LOW_PATHS + list(low_paths or []))
    why = t_why + p_why
    if "high" in (t_tier, p_tier):
        return Risk("high", why)
    if p_tier == "low" or (t_tier == "low" and p_tier is None):
        return Risk("low", why)
    return Risk("normal", why or ["no high-risk signal"])


def higher(a: str, b: str) -> str:
    return a if TIERS.index(a) >= TIERS.index(b) else b
