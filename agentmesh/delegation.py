"""Build a Task for a role from project config (shared by `delegate` and `run-batch`)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import ProjectConfig
from .errors import AgentMeshError
from .models import Task
from .risk import Risk, assess, higher
from .roles import GLOBAL_FORBIDDEN
from .task_manager import TaskManager


@dataclass
class TaskSpec:
    role: str
    title: str = ""
    description: str = ""
    requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    worker: str | None = None
    avoid_workers: list[str] = field(default_factory=list)
    parent: str | None = None
    reuse_worktree: str | None = None
    base_tasks: list[str] = field(default_factory=list)
    context_tasks: list[str] = field(default_factory=list)
    inplace: bool = False
    timeout: int | None = None
    risk: str | None = None             # override; else classified from title+description


def risk_for(cfg: ProjectConfig, spec: TaskSpec) -> Risk:
    text = f"{spec.title}\n{spec.description}"
    return assess(text, [], override=spec.risk, high_paths=cfg.get("risk.high_paths", []),
                  low_paths=cfg.get("risk.low_paths", []))


def inherited_risk(tm: TaskManager, spec: TaskSpec, own: str) -> str:
    """A test/review task is as risky as the work it checks."""
    tier = own
    for tid in [spec.reuse_worktree, *spec.base_tasks, *spec.context_tasks]:
        if tid:
            tier = higher(tier, tm.load(tid).risk)
    return tier


def build_task(cfg: ProjectConfig, tm: TaskManager, spec: TaskSpec) -> Task:
    roles = cfg.roles()
    if spec.role not in roles:
        raise AgentMeshError(f"role '{spec.role}' is not part of this project. Available: {', '.join(roles)}")
    policy = roles[spec.role]
    read_only = policy.get("capability") == "read-only"
    title = spec.title or (spec.description.strip().splitlines()[0][:80] if spec.description.strip() else spec.role)
    stacked = bool(spec.base_tasks)
    task = Task(
        task_id=tm.next_id(), title=title, role=spec.role, description=spec.description,
        capability=policy.get("capability", "write"), requirements=spec.requirements, constraints=spec.constraints,
        acceptance_criteria=spec.acceptance,
        verification=spec.verify or [f"{v['run']}  (in {v['cwd']})" for v in cfg.get("verification.commands", [])][:6],
        project_root=str(cfg.paths.root), allowed_paths=policy.get("allowed_paths", []) + spec.allow,
        forbidden_paths=policy.get("forbidden_paths", GLOBAL_FORBIDDEN),
        preferred_worker=spec.worker, avoid_workers=spec.avoid_workers, parent_task=spec.parent,
        reuse_worktree_of=spec.reuse_worktree, base_tasks=spec.base_tasks, context_tasks=spec.context_tasks,
        isolation="inplace" if (spec.inplace or (read_only and not spec.reuse_worktree and not stacked))
        else policy.get("isolation", "worktree"),
        timeout_seconds=spec.timeout or int(cfg.get("workers.timeout_seconds", 1800)),
        risk=spec.risk or inherited_risk(tm, spec, risk_for(cfg, spec).tier))
    tm.save(task)
    return task
