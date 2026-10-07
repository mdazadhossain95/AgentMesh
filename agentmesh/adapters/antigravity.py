from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


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
