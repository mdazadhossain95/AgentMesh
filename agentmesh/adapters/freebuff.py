from __future__ import annotations

from ..errors import UnsupportedError
from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class FreebuffAdapter(AgentAdapter):
    name = "freebuff"
    display_name = "Freebuff"
    executables = ("freebuff",)
    interactive_only = True
    cwd_flag = "--cwd"
    continue_flag = "--continue"

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        raise UnsupportedError("freebuff exposes no non-interactive mode in --help; it cannot be a worker")
