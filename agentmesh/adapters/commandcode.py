from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class CommandCodeAdapter(AgentAdapter):
    name = "command-code"
    display_name = "Command Code"
    executables = ("command-code", "cmd")
    # Observed on this project's dev machine: `command-code --version` triggered a self-update.
    version_args = None
    required_flags = ("--print", "--permission-mode")
    structured_flags = ("--output-format",)
    output_formats = ("json",)
    continue_flag = "--continue"

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--print")
        argv = [info.path or "command-code"]
        if ctx.autonomy == "full" and self.has(info, "--yolo"):
            argv.append("--yolo")
        elif self.has(info, "--permission-mode"):
            argv += ["--permission-mode", "accept-edits"]
        if self.has(info, "--trust"):
            argv.append("--trust")
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        argv += ctx.extra_args
        argv += ["--print", ctx.prompt]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
