"""Config-driven adapter for any CLI: new tools need no code, only an agents.yaml entry."""
from __future__ import annotations

from typing import Any

from ..errors import UnsupportedError
from ..models import AgentInfo, WorkerState
from .base import AgentAdapter, CommandSpec, RunContext


class GenericAdapter(AgentAdapter):
    """
    agents.yaml entry:
        mytool:
          executable: mytool
          display_name: My Tool
          headless_args: ["--print", "{prompt}"]   # tokens: {prompt} {cwd} {model} {timeout}
          edit_args: ["--auto-edit"]                # optional, added when autonomy=edit
          full_args: ["--yolo"]                     # optional, added when autonomy=full
          continue_args: ["--continue"]             # optional
          stdin_prompt: false                       # true => prompt sent on stdin ({prompt} unused)
          version_args: ["--version"]
          structured_output: false
    Nothing here is assumed: if `headless_args` is absent the worker is CONFIGURATION_REQUIRED.
    """

    def __init__(self, name: str, spec: dict[str, Any]):
        super().__init__()
        self.name = name
        self.spec = spec
        self.display_name = spec.get("display_name", name)
        self.executables = (spec.get("executable", name),)
        va = spec.get("version_args", ["--version"])
        self.version_args = tuple(va) if va else None
        self.requires_config = not spec.get("headless_args") and not spec.get("stdin_prompt")

    def capabilities(self, info: AgentInfo, help_text: str) -> None:
        info.notes.append("generic adapter: invocation comes from user config, not verified by AgentMesh")
        if self.requires_config:
            super().capabilities(info, help_text)
            return
        info.headless, info.state = "YES", WorkerState.READY.value
        info.structured_output = "YES" if self.spec.get("structured_output") else "NOT_DOCUMENTED"
        info.session_continue = "YES" if self.spec.get("continue_args") else "NOT_DOCUMENTED"
        info.cwd_behavior = "PROCESS_CWD"
        info.native_timeout = "NO (AgentMesh enforces)"

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        if self.requires_config:
            raise UnsupportedError(f"{self.name}: CONFIGURATION_REQUIRED (no headless_args)")
        tokens = {"prompt": ctx.prompt, "cwd": str(ctx.cwd), "model": ctx.model or "", "timeout": str(ctx.timeout)}

        def fmt(args: list[str]) -> list[str]:
            return [a.format(**tokens) for a in args]

        argv = [info.path or self.executables[0]]
        argv += fmt(self.spec.get("full_args" if ctx.autonomy == "full" else "edit_args", []))
        if ctx.continue_session:
            argv += fmt(self.spec.get("continue_args", []))
        argv += ctx.extra_args
        argv += fmt(self.spec.get("headless_args", []))
        stdin = ctx.prompt if self.spec.get("stdin_prompt") else None
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, stdin=stdin,
                           env={**self.spec.get("env", {}), **ctx.env})
