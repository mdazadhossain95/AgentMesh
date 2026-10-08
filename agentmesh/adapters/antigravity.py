from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext, probe_command

# Live benchmark 2026-10-08 (4 hidden-test tasks): the CLI's default model scored 38-62%, gemini-3.1-pro-high and
# gemini-3.7-flash-high 75% (3.7 is faster), gemini-3.8-flash-high 0%; claude/gpt-oss models hit QUOTA_EXCEEDED.
PREFERRED_MODELS = ("gemini-3.7-flash-high", "gemini-3.1-pro-high")


def available_models(path: str | None = None) -> list[str]:
    code, out = probe_command([path or "agy", "models"], timeout=45)
    if code != 0:
        return []
    return [line.split("\t")[0].strip() for line in out.splitlines() if "\t" in line]


def available_preferred_models(path: str | None = None) -> list[str]:
    have = set(available_models(path))
    return [m for m in PREFERRED_MODELS if m in have]


class AntigravityAdapter(AgentAdapter):
    name = "antigravity"
    display_name = "Google Antigravity CLI"
    executables = ("agy",)
    required_flags = ("--print", "--output-format")
    structured_flags = ("--output-format",)
    output_formats = ("json", "stream-json")
    continue_flag = "--continue"
    timeout_flag = "--print-timeout"
    notes = ("prompt is passed as the value of --print (inferred from help text; confirm with `agentmesh smoke antigravity`)",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--print")
        argv = [info.path or "agy"]
        if ctx.autonomy == "full" and self.has(info, "--dangerously-skip-permissions"):
            argv.append("--dangerously-skip-permissions")
        elif self.has(info, "--mode"):
            argv += ["--mode", "accept-edits"]
        if self.has(info, "--print-timeout"):
            argv += ["--print-timeout", f"{ctx.timeout}s"]
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv += ["--print", ctx.prompt]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
