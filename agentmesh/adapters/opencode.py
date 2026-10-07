from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


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
    notes = ("`run` has no edit-only approval flag; autonomy=full adds --auto (approve everything not denied)",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--dir")
        argv = [info.path or "opencode", "run", "--dir", str(ctx.cwd)]
        if ctx.autonomy == "full" and self.has(info, "--auto"):
            argv.append("--auto")
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv.append(ctx.prompt)
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
