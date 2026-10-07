from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class GeminiAdapter(AgentAdapter):
    name = "gemini"
    display_name = "Gemini CLI"
    executables = ("gemini",)
    required_flags = ("--prompt", "--approval-mode")
    structured_flags = ("--output-format",)
    output_formats = ("json", "stream-json")

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--prompt")
        argv = [info.path or "gemini"]
        if self.has(info, "--approval-mode"):
            argv += ["--approval-mode", "yolo" if ctx.autonomy == "full" else "auto_edit"]
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv += ["--prompt", ctx.prompt]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
