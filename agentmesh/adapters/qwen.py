from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class QwenAdapter(AgentAdapter):
    name = "qwen"
    display_name = "Qwen Code"
    executables = ("qwen",)
    required_flags = ("--prompt",)
    structured_flags = ("--output-format",)
    output_formats = ("json", "stream-json")
    continue_flag = "--continue"
    notes = ("--help shows no approval/permission flag: whether edits are auto-approved headlessly "
             "depends on Qwen settings; pass one via workers.extra_args.qwen if needed",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--prompt")
        argv = [info.path or "qwen"]
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv += ["--prompt", ctx.prompt]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
