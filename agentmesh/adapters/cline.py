from __future__ import annotations

from ..errors import UnsupportedError
from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class ClineAdapter(AgentAdapter):
    name = "cline"
    display_name = "Cline CLI"
    executables = ("cline",)
    login_args = ("auth",)
    required_flags = ("--cwd", "--timeout")
    structured_flags = ("--json",)
    output_formats = ("json",)
    cwd_flag = "--cwd"
    timeout_flag = "--timeout"
    notes = ("headless runs auto-approve all tools and no edit-only mode is documented: cline is refused under "
             "autonomy=edit and only runs when autonomy=full is set explicitly (workers.role_autonomy / workers.autonomy)",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--cwd")
        if ctx.autonomy != "full":
            # fail closed: cline cannot enforce edit-only, so never run it with silent full approval
            raise UnsupportedError("cline cannot enforce edit-only autonomy; set autonomy=full explicitly to use it")
        argv = [info.path or "cline", "--cwd", str(ctx.cwd)]
        if self.has(info, "--timeout"):
            argv += ["--timeout", str(ctx.timeout)]
        if self.has(info, "--yolo"):
            argv.append("--yolo")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv += ["--", ctx.prompt]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
