from __future__ import annotations

from ..models import AgentInfo
from .base import RunContext, CommandSpec, probe_command
from .opencode import OpenCodeAdapter

# Free (":free") models that answered a coding prompt in a live test on 2026-10-07, ordered by speed with the one
# code-specialised model and the largest one kept near the top. Responsiveness on one account, not a quality benchmark.
PREFERRED_MODELS: tuple[str, ...] = (
    "kilo/nvidia/nemotron-3-super-120b-a12b:free",
    "kilo/cohere/north-mini-code:free",
    "kilo/poolside/laguna-s-2.1:free",
    "kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
    "kilo/stepfun/step-3.7-flash:free",
)


def available_models(path: str | None = None) -> list[str]:
    code, out = probe_command([path or "kilo", "models"], timeout=45)
    return [ln.strip() for ln in out.splitlines() if "/" in ln] if code == 0 else []


def available_preferred_models(path: str | None = None) -> list[str]:
    have = set(available_models(path))
    return [m for m in PREFERRED_MODELS if m in have]


class KiloAdapter(OpenCodeAdapter):
    """Kilo CLI (`@kilocode/cli`) is an OpenCode fork: same `run [message] --dir --model --auto` interface,
    confirmed from `kilo run --help` (7.8.8)."""
    name = "kilo"
    display_name = "Kilo CLI"
    executables = ("kilo", "kilocode")
    install_url = "https://kilo.ai/docs/code-with-ai/platforms/cli"
    install_argv = ("npm", "install", "-g", "@kilocode/cli")
    install_needs = "npm"
    notes = ("`run` has no edit-only approval flag; autonomy=full adds --auto (approve everything not denied)",
             "set workers.models.kilo to ranked ':free' models (init seeds them)")

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        spec = super().build_command(ctx, info)
        spec.argv[0] = info.path or "kilo"
        return spec
