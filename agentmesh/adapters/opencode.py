from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext, probe_command


# Ranked by a live check on 2026-10-07 (answered a coding prompt; fastest first). Ordering is a measurement of
# responsiveness on one key, NOT a quality benchmark. Re-run your own: `opencode models`, `agentmesh smoke opencode --model ...`.
PREFERRED_MODELS = (
    "nvidia/nvidia/nemotron-3-super-120b-a12b",
    "nvidia/openai/gpt-oss-20b",
    "opencode/big-pickle",
    "nvidia/poolside/laguna-xs-2.1",
    "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b",
)


# Extra models worth measuring that timed out under parallel load in the first (single-prompt) check.
EXTRA_CANDIDATES = ("nvidia/moonshotai/kimi-k3", "nvidia/z-ai/glm-5.3", "nvidia/deepseek-ai/deepseek-v4.1-flash")


def available_models(path: str | None = None) -> list[str]:
    """Every model this machine's opencode lists (local, free)."""
    code, out = probe_command([path or "opencode", "models"], timeout=30)
    return [ln.strip() for ln in out.splitlines() if "/" in ln] if code == 0 else []


def available_preferred_models(path: str | None = None) -> list[str]:
    have = set(available_models(path))
    return [m for m in PREFERRED_MODELS if m in have]


class OpenCodeAdapter(AgentAdapter):
    name = "opencode"
    display_name = "OpenCode"
    executables = ("opencode",)
    headless_help_args = ("run", "--help")
    required_flags = ("--dir",)
    structured_flags = ("--format",)
    output_formats = ("json",)
    continue_flag = "--continue"
    cwd_flag = "--dir"
    notes = ("observed: with its configured default model the run hung until timeout while `-m opencode/big-pickle` answered; "
             "set workers.models.opencode explicitly",
             "`run` has no edit-only approval flag; autonomy=full adds --auto (approve everything not denied)",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--dir")
        argv = [info.path or self.executables[0], "run", "--dir", str(ctx.cwd)]
        if ctx.autonomy == "full" and self.has(info, "--auto"):
            argv.append("--auto")
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv.append(ctx.prompt)
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
