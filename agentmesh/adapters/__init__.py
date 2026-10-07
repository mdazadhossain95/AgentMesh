from __future__ import annotations

from .antigravity import AntigravityAdapter
from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .commandcode import CommandCodeAdapter
from .freebuff import FreebuffAdapter
from .gemini import GeminiAdapter
from .generic import GenericAdapter
from .kilo import KiloAdapter
from .mock import MockAdapter
from .opencode import OpenCodeAdapter
from .qwen import QwenAdapter

BUILTIN_ADAPTERS: tuple[type[AgentAdapter], ...] = (
    ClaudeAdapter, CodexAdapter, AntigravityAdapter, QwenAdapter, GeminiAdapter,
    OpenCodeAdapter, CommandCodeAdapter, KiloAdapter, FreebuffAdapter,
)

__all__ = ["AgentAdapter", "BUILTIN_ADAPTERS", "GenericAdapter", "MockAdapter"]
