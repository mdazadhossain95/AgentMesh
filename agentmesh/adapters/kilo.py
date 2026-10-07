from __future__ import annotations

from ..errors import UnsupportedError
from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class KiloAdapter(AgentAdapter):
    """Placeholder: Kilo's headless interface could not be inspected on the author's machine
    (not installed), so no flags are asserted. Define a command in ~/.agentmesh/agents.yaml
    under the name `kilo` and it replaces this adapter."""
    name = "kilo"
    display_name = "Kilo"
    executables = ("kilo", "kilocode")
    requires_config = True

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        raise UnsupportedError("kilo: CONFIGURATION_REQUIRED (define `headless_args` in agents.yaml)")
