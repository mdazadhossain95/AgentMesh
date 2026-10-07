"""Run many tasks with dependencies: independent ones in parallel, dependent ones after their inputs."""
from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import ProjectConfig
from .delegation import TaskSpec, build_task
from .errors import AgentMeshError, ErrorCode
from .models import WorkerResult
from .runner import Runner
from .task_manager import TaskManager


@dataclass
class BatchItem:
    id: str
    spec: TaskSpec
    depends_on: list[str] = field(default_factory=list)


@dataclass
class BatchOutcome:
    id: str
    task_id: str | None
    status: str                       # SUCCESS | FAILED | SCOPE_VIOLATION | SKIPPED
    result: WorkerResult | None = None
    note: str = ""


def load_batch(path: Path) -> list[BatchItem]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise AgentMeshError(f"cannot read batch file {path}: {e}", ErrorCode.PROJECT_ERROR) from e
    items = []
    for t in raw.get("tasks", []):
        if "id" not in t or "role" not in t:
            raise AgentMeshError("every batch task needs 'id' and 'role'", ErrorCode.PROJECT_ERROR)
        spec = TaskSpec(
            role=t["role"], title=t.get("title", ""), description=t.get("description", ""),
            requirements=t.get("requirements", []), constraints=t.get("constraints", []),
            acceptance=t.get("acceptance", []), verify=t.get("verify", []), allow=t.get("allow", []),
            worker=t.get("worker"), avoid_workers=t.get("avoid_workers", []), timeout=t.get("timeout"))
        items.append(BatchItem(t["id"], spec, list(t.get("depends_on", []))))
    validate(items)
    return items


def validate(items: list[BatchItem]) -> None:
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise AgentMeshError("duplicate task ids in batch", ErrorCode.PROJECT_ERROR)
    for i in items:
        for d in i.depends_on:
            if d not in ids:
                raise AgentMeshError(f"task '{i.id}' depends on unknown '{d}'", ErrorCode.PROJECT_ERROR)
    # cycle check (Kahn)
    left = {i.id: set(i.depends_on) for i in items}
    while left:
        ready = [k for k, v in left.items() if not v]
        if not ready:
            raise AgentMeshError(f"dependency cycle among: {', '.join(sorted(left))}", ErrorCode.PROJECT_ERROR)
        for k in ready:
            del left[k]
        for v in left.values():
            v.difference_update(ready)


def run_batch(runner: Runner, cfg: ProjectConfig, items: list[BatchItem], *, max_parallel: int = 3,
              fail_fast: bool = False, emit: Callable[[str], None] = lambda m: None) -> dict[str, BatchOutcome]:
    tm = TaskManager(cfg.paths)
    roles = cfg.roles()
    by_id = {i.id: i for i in items}
    outcomes: dict[str, BatchOutcome] = {}
    pending = [i.id for i in items]
    running: dict[Future[WorkerResult], str] = {}
    started: dict[str, str] = {}
    stop = False

    def prepare(item: BatchItem) -> TaskSpec:
        """Turn dependencies into inputs: code-producing deps become the base to build on, others are context."""
        spec = TaskSpec(**{**item.spec.__dict__})
        for dep in item.depends_on:
            dep_task = tm.load(outcomes[dep].task_id)            # type: ignore[arg-type]
            if dep_task.capability == "write" and dep_task.branch and not dep_task.reuse_worktree_of:
                spec.base_tasks.append(dep_task.task_id)
            else:
                spec.context_tasks.append(dep_task.task_id)
        if roles.get(spec.role, {}).get("capability") == "read-only" and spec.role.endswith("reviewer"):
            # independent eyes: keep reviewers off the CLI that wrote the code, when another is available
            for tid in spec.base_tasks:
                r = tm.load_result(tid)
                if r and r.worker:
                    spec.avoid_workers.append(r.worker)
        return spec

    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        try:
            while pending or running:
                for tid in list(pending):
                    item = by_id[tid]
                    if not all(d in outcomes for d in item.depends_on):
                        continue
                    bad = [d for d in item.depends_on if outcomes[d].status != "SUCCESS"]
                    if bad or stop:
                        why = f"dependency not successful: {', '.join(bad)}" if bad else "stopped (fail-fast)"
                        outcomes[tid] = BatchOutcome(tid, None, "SKIPPED", None, why)
                        pending.remove(tid)
                        emit(f"[batch] {tid} skipped: {why}")
                    elif len(running) < max_parallel:
                        try:
                            task = build_task(cfg, tm, prepare(item))
                        except AgentMeshError as e:
                            outcomes[tid] = BatchOutcome(tid, None, "FAILED", None, str(e))
                            pending.remove(tid)
                            continue
                        pending.remove(tid)
                        emit(f"[batch] start {tid} ({task.task_id}, role={task.role})")
                        fut = pool.submit(runner.run, task)
                        running[fut] = tid
                        started[tid] = task.task_id          # not in `outcomes` until finished
                if not running:
                    continue              # nothing in flight: re-sweep (skips may have unblocked others)
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    tid = running.pop(fut)
                    try:
                        res = fut.result()
                        outcomes[tid] = BatchOutcome(tid, res.task_id, res.status, res, res.summary[:200])
                    except AgentMeshError as e:
                        outcomes[tid] = BatchOutcome(tid, started.get(tid), "FAILED", None, str(e))
                    emit(f"[batch] done  {tid}: {outcomes[tid].status}")
                    if fail_fast and outcomes[tid].status != "SUCCESS":
                        stop = True
        except KeyboardInterrupt:
            for a in runner.registry.adapters():
                for run_id in list(a._procs):
                    a.cancel(run_id)
            raise
    # keep declared order
    return {i.id: outcomes[i.id] for i in items}


def template_from_plan(plan_dict: dict[str, Any], request: str) -> dict[str, Any]:
    """A batch file for a plan: implementers run in parallel, tests wait for all of them, reviews for tests."""
    stages = [s for s in plan_dict["stages"] if s["role"] and not s["conditional"]]
    ids = {s["id"] for s in stages}
    impl = [s["id"] for s in stages if s["id"].startswith("implement-")]
    early = [x for x in ("spec", "contract") if x in ids]
    tasks = []
    for s in stages:
        sid = s["id"]
        if sid == "spec": deps = []
        elif sid == "contract": deps = ["spec"] if "spec" in ids else []
        elif sid in impl: deps = early[-1:]
        elif sid == "test": deps = impl
        else: deps = impl + (["test"] if "test" in ids else [])      # reviews
        tasks.append({"id": sid, "role": s["role"], "title": f"{sid}: {request[:60]}", "description": request,
                      "acceptance": [], "depends_on": deps})
    return {"tasks": tasks}
