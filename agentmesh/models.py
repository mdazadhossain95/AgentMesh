"""Typed models: tasks, worker results, worker info."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkerState(str, Enum):
    READY = "READY"                              # installed + headless invocation documented by its own --help
    INTERACTIVE_ONLY = "INTERACTIVE_ONLY"        # no non-interactive mode found
    CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"
    UNVERIFIED = "UNVERIFIED"                    # detected, expected flags not confirmed
    NOT_INSTALLED = "NOT_INSTALLED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    INTEGRATED = "INTEGRATED"
    ABANDONED = "ABANDONED"


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in data.items() if k in names})  # type: ignore[call-arg]


def _plain(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


@dataclass
class AgentInfo(_Serializable):
    """What discovery learned about one CLI on this machine. Never guessed."""
    name: str
    display_name: str
    adapter: str
    installed: bool = False
    executable: str | None = None
    path: str | None = None
    version: str | None = None
    state: str = WorkerState.NOT_INSTALLED.value
    headless: str = "UNKNOWN"            # YES / NO / UNKNOWN
    structured_output: str = "UNKNOWN"
    output_formats: list[str] = field(default_factory=list)
    session_continue: str = "UNKNOWN"
    cwd_behavior: str = "UNKNOWN"
    auth: str = "UNKNOWN"                # never probed without a paid call
    native_timeout: str = "UNKNOWN"
    smoke: str = "NOT_RUN"               # NOT_RUN / PASSED / FAILED (user-triggered live check)
    flags: list[str] = field(default_factory=list)   # flags seen in --help
    notes: list[str] = field(default_factory=list)
    probed_at: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.state == WorkerState.READY.value


@dataclass
class Task(_Serializable):
    task_id: str
    title: str
    role: str
    description: str = ""
    capability: str = "write"            # read-only | write
    requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    project_root: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    preferred_worker: str | None = None
    avoid_workers: list[str] = field(default_factory=list)
    retry_count: int = 0
    parent_task: str | None = None
    reuse_worktree_of: str | None = None
    context_tasks: list[str] = field(default_factory=list)  # earlier tasks whose summaries are given as input
    base_tasks: list[str] = field(default_factory=list)   # start from these tasks' branches (stacked work)
    isolation: str = "worktree"          # worktree | inplace
    delegation_depth: int = 1
    timeout_seconds: int = 1800
    status: str = TaskStatus.PENDING.value
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    verified: bool = False
    created_at: str = field(default_factory=utcnow)


@dataclass
class Attempt(_Serializable):
    worker: str
    exit_code: int | None
    normalized_error: str | None
    duration_seconds: float
    note: str = ""


@dataclass
class WorkerResult(_Serializable):
    task_id: str
    role: str
    worker: str | None
    status: str                          # SUCCESS | FAILED | SCOPE_VIOLATION
    exit_code: int | None = None
    summary: str = ""
    worktree: str | None = None
    branch: str | None = None
    changed_files: list[str] = field(default_factory=list)   # computed from git, not worker text
    reported_files: list[str] = field(default_factory=list)  # what the worker claimed
    tests: list[dict[str, Any]] = field(default_factory=list)
    normalized_error: str | None = None
    policy_violations: list[str] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    session_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    started_at: str = ""
    finished_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerResult":
        data = dict(data)
        data["attempts"] = [Attempt.from_dict(a) for a in data.get("attempts", [])]
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})
