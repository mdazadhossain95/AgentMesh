"""Adapter registry. Adding a CLI = one adapter + one register() call (or an agents.yaml entry)."""
from __future__ import annotations

import os
from typing import Any

from .adapters import BUILTIN_ADAPTERS, AgentAdapter, GenericAdapter, MockAdapter
from .config import load_custom_agents
from .errors import AgentMeshError

ALIASES = {"agy": "antigravity", "cmd": "command-code", "claude-code": "claude", "kilocode": "kilo",
           "commandcode": "command-code", "kiro-cli": "kiro"}


class Registry:
    def __init__(self) -> None:
        self._adapters: dict[str, AgentAdapter] = {}

    def register(self, adapter: AgentAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def resolve(self, name: str) -> str:
        return ALIASES.get(name, name)

    def get(self, name: str) -> AgentAdapter:
        key = self.resolve(name)
        if key not in self._adapters:
            raise AgentMeshError(f"unknown worker '{name}'. Known: {', '.join(self.names())}")
        return self._adapters[key]

    def has(self, name: str) -> bool:
        return self.resolve(name) in self._adapters

    def names(self) -> list[str]:
        return list(self._adapters)

    def adapters(self) -> list[AgentAdapter]:
        return list(self._adapters.values())

    @property
    def known_executables(self) -> set[str]:
        return {e for a in self._adapters.values() for e in a.executables}


def build_registry(custom: dict[str, dict[str, Any]] | None = None, *, mock_env: bool = True) -> Registry:
    reg = Registry()
    for cls in BUILTIN_ADAPTERS:
        reg.register(cls())
    for name, spec in (load_custom_agents() if custom is None else custom).items():
        reg.register(GenericAdapter(name, spec or {}))     # overrides a built-in of the same name
    spec_env = os.environ.get("AGENTMESH_MOCK", "") if mock_env else ""
    for item in filter(None, (s.strip() for s in spec_env.split(","))):
        name, _, behaviors = item.partition(":")
        write = os.environ.get("AGENTMESH_MOCK_WRITE")
        reg.register(MockAdapter(name, behaviors.split("+") if behaviors else "SUCCESS",
                                 {write: "mock\n"} if write else None))
    return reg
