"""Global (~/.agentmesh) and project (.agentmesh/) configuration."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ErrorCode, ProjectError

SCHEMA_VERSION = 1

# Keys regenerated on every `init`; everything else is human-owned and preserved.
MACHINE_KEYS = ("schema", "project", "detected", "roles", "verification")

DEFAULT_QUALITY_ORDER = ["claude", "codex", "antigravity", "opencode", "cline", "kiro", "copilot", "kilo"]

DEFAULTS: dict[str, Any] = {
    "manager": {"default": "claude", "bootstrap_files": ["CLAUDE.md", "AGENTS.md"]},
    "routing": {
        "strategy": "balanced",            # balanced | quality-first | free-first | preferred-order
        "preferred_order": [],
        "quality_order": DEFAULT_QUALITY_ORDER,   # a configured preference, not a benchmark
        "free_workers": [],                # workers YOU know are free for you; AgentMesh does not guess
        "disabled": [],
        "allow_unverified": False,
        "roles": {},                       # role -> ordered worker list
    },
    "fallback": {
        "enabled": True,
        "max_attempts": 3,
        "on": [c.value for c in (
            ErrorCode.QUOTA_EXCEEDED, ErrorCode.RATE_LIMITED, ErrorCode.AUTH_FAILED,
            ErrorCode.CLI_NOT_FOUND, ErrorCode.TIMEOUT, ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.UNSUPPORTED)],
        "cooldown_seconds": {
            "QUOTA_EXCEEDED": 3600, "RATE_LIMITED": 300, "AUTH_FAILED": 3600,
            "CLI_NOT_FOUND": 3600, "TIMEOUT": 0, "PROVIDER_UNAVAILABLE": 120, "UNSUPPORTED": 3600,
        },
    },
    "delegation": {"max_depth": 1},        # USER -> MANAGER -> WORKER; workers may not delegate
    "workers": {
        "autonomy": "edit",                # edit | full  (full = CLI's permission-bypass flag)
        "timeout_seconds": 1800,
        "extra_args": {},                  # worker -> [args] appended verbatim
        "role_autonomy": {},               # role -> edit | full
        "models": {},                      # worker -> model name
    },
    "worktree": {"enabled": True, "dir": ".agentmesh-worktrees", "auto_commit": True},
    "enforcement": {                       # makes the manager follow the loop (Claude Code hooks; `audit` for any CLI)
        "mode": "block",                   # block | warn | off
        "allow_paths": [".agentmesh/**", ".claude/**", ".gitignore", "CLAUDE.md", "AGENTS.md", "**/*.md", "docs/**"],
        "stop_check": True,                # Stop hook refuses to finish while recent tasks are unverified
        "stop_window_hours": 12,
        "claude_hooks": True,              # init writes .claude/settings.json
    },
    "parallel": {"max_workers": 3},         # run-batch: tasks at once
    "references": [],
    "overrides": {"roles": {}},            # per-role patches applied over generated `roles`
}


def global_home() -> Path:
    return Path(os.environ.get("AGENTMESH_HOME", Path.home() / ".agentmesh"))


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProjectError(f"invalid YAML in {path}: {e}") from e


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100)


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """over wins; dicts merge recursively; lists/scalars replace."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def mesh(self) -> Path: return self.root / ".agentmesh"
    @property
    def project_yaml(self) -> Path: return self.mesh / "project.yaml"
    @property
    def workflow_yaml(self) -> Path: return self.mesh / "workflow.yaml"
    @property
    def agents_dir(self) -> Path: return self.mesh / "agents"
    @property
    def tasks_dir(self) -> Path: return self.mesh / "tasks"
    @property
    def reports_dir(self) -> Path: return self.mesh / "reports"
    @property
    def references_dir(self) -> Path: return self.mesh / "references"
    @property
    def runtime_dir(self) -> Path: return self.mesh / "runtime"
    @property
    def state_dir(self) -> Path: return self.mesh / "state"
    @property
    def logs_dir(self) -> Path: return self.mesh / "logs"


def find_project_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".agentmesh" / "project.yaml").is_file():
            return d
    return None


class ProjectConfig:
    """Thin dict wrapper: project.yaml merged over DEFAULTS, with dotted access."""

    def __init__(self, paths: ProjectPaths, data: dict[str, Any]):
        self.paths = paths
        self.data = deep_merge(DEFAULTS, data)

    @classmethod
    def load(cls, root: Path) -> "ProjectConfig":
        paths = ProjectPaths(root)
        if not paths.project_yaml.is_file():
            raise ProjectError(f"{root} is not an AgentMesh project (run `agentmesh init --auto`)")
        return cls(paths, load_yaml(paths.project_yaml))

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @property
    def name(self) -> str:
        return self.get("project.name", self.paths.root.name)

    @property
    def kind(self) -> str:
        return self.get("project.kind", "generic")

    def roles(self) -> dict[str, dict[str, Any]]:
        generated = self.get("roles", {}) or {}
        patches = self.get("overrides.roles", {}) or {}
        return {r: deep_merge(spec, patches.get(r, {})) for r, spec in generated.items()}

    def role_autonomy(self, role: str) -> str:
        return self.get("workers.role_autonomy", {}).get(role) or self.get("workers.autonomy", "edit")


def set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            raise ProjectError(f"cannot set {dotted}: {p} is not a mapping")
    cur[parts[-1]] = value


def parse_scalar(text: str) -> Any:
    """CLI values: JSON if it parses (true, 3, [..]), else plain string."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def load_custom_agents() -> dict[str, dict[str, Any]]:
    """User-defined generic adapters from ~/.agentmesh/agents.yaml."""
    path = global_home() / "agents.yaml"
    if not path.is_file():
        return {}
    return (load_yaml(path) or {}).get("agents", {}) or {}
