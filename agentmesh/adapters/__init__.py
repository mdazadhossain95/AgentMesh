from __future__ import annotations

from .antigravity import AntigravityAdapter
from .base import AgentAdapter
from .claude import ClaudeAdapter
from .cline import ClineAdapter
from .codex import CodexAdapter
from .copilot import CopilotAdapter
from .freebuff import FreebuffAdapter
from .generic import GenericAdapter
from .kilo import KiloAdapter
from .kiro import KiroAdapter
from .mock import MockAdapter
from .opencode import OpenCodeAdapter

BUILTIN_ADAPTERS: tuple[type[AgentAdapter], ...] = (
    ClaudeAdapter, CodexAdapter, AntigravityAdapter,
    OpenCodeAdapter, ClineAdapter, KiroAdapter, CopilotAdapter, KiloAdapter, FreebuffAdapter,
)

__all__ = ["AgentAdapter", "BUILTIN_ADAPTERS", "GenericAdapter", "MockAdapter"]
