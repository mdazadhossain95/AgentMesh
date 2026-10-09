"""Run one task with role/worker separation and automatic fallback."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from . import gitutil, scope
from .adapters.base import RunContext
from .config import ProjectConfig
from .errors import AgentMeshError, ErrorCode, INFRA_ERRORS, UnsupportedError, parse_retry_seconds
from .benchmark import load_saved
from .models import Attempt, AgentInfo, Task, TaskStatus, WorkerResult, utcnow
from .progress import Heartbeat, typical_seconds
from .prompting import build_prompt, parse_report, read_role
from .registry import Registry
from .router import Router
from .security import redact
from .workflow import REVIEW_ROLES
from .state import RuntimeState
from .task_manager import TaskManager
from .worktree import Worktrees

DEPTH_ENV = "AGENTMESH_DEPTH"


def current_depth() -> int:
    try:
        return int(os.environ.get(DEPTH_ENV, "0"))
    except ValueError:
        return 0


def check_depth(config: ProjectConfig) -> None:
    max_depth = int(config.get("delegation.max_depth", 1))
    if current_depth() >= max_depth:
        raise AgentMeshError(
            f"delegation depth {current_depth()} >= max_depth {max_depth}: workers may not delegate "
            "(raise delegation.max_depth in .agentmesh/project.yaml to allow nesting)", ErrorCode.UNSUPPORTED)


class Runner:
    def __init__(self, config: ProjectConfig, registry: Registry, infos: dict[str, AgentInfo],
                 state: RuntimeState, tasks: TaskManager | None = None,
                 on_event: Callable[[str], None] | None = None):
        self.config, self.registry, self.infos, self.state = config, registry, infos, state
        self.root = config.paths.root
        self.tasks = tasks or TaskManager(config.paths)
        self.router = Router(registry, infos, config, state)
        self.worktrees = Worktrees(self.root, config.get("worktree.dir", ".agentmesh-worktrees"))
        self.emit = on_event or (lambda _m: None)

    # ---- workspace ----
    def _prepare_workspace(self, task: Task) -> tuple[Path, str | None, bool]:
        """Returns (cwd, owner_task_id_for_worktree|None, owns_base)."""
        if task.reuse_worktree_of:
            owner = self.tasks.load(task.reuse_worktree_of)
            if not owner.worktree or not Path(owner.worktree).is_dir():
                raise AgentMeshError(f"{owner.task_id} has no live worktree to reuse")
            task.worktree, task.branch = owner.worktree, owner.branch
            task.base_commit = gitutil.head(Path(owner.worktree))
            return Path(owner.worktree), owner.task_id, False
        if task.worktree and Path(task.worktree).is_dir():           # continuation
            return Path(task.worktree), task.task_id, True
        if task.base_tasks:
            branches = self._base_branches(task)
            path, branch, base = self.worktrees.create(task.task_id, branches[0] if branches else "HEAD",
                                                       tuple(branches[1:]))
            task.worktree, task.branch, task.base_commit = str(path), branch, base
            return path, task.task_id, True
        if task.isolation == "worktree" and self.config.get("worktree.enabled", True):
            path, branch, base = self.worktrees.create(task.task_id)
            task.worktree, task.branch, task.base_commit = str(path), branch, base
            return path, task.task_id, True
        if gitutil.repo_root(self.root) and gitutil.has_commits(self.root):
            task.base_commit = gitutil.head(self.root)
        return self.root, None, False

    def _inputs(self, task: Task) -> list[WorkerResult]:
        out = []
        for tid in dict.fromkeys([*task.context_tasks, *task.base_tasks]):
            r = self.tasks.load_result(tid)
            if r is not None:
                out.append(r)
        return out

    def _base_branches(self, task: Task) -> list[str]:
        """Branches of the tasks this one builds on. Integrated ones are already in HEAD and are skipped."""
        out: list[str] = []
        for tid in task.base_tasks:
            base = self.tasks.load(tid)
            if base.status == TaskStatus.INTEGRATED.value:
                continue
            if base.status != TaskStatus.SUCCESS.value or not base.branch or base.reuse_worktree_of:
                raise AgentMeshError(f"base task {tid} is {base.status}; only SUCCESS tasks that own a branch can be built on",
                                     ErrorCode.PROJECT_ERROR)
            out.append(base.branch)
        return out

    def _changed(self, cwd: Path, ref: str | None, pre_dirty: set[str]) -> list[str]:
        if ref is None or gitutil.repo_root(cwd) is None:
            return []
        return [f for f in gitutil.changed_since(cwd, ref) if f not in pre_dirty
                and not f.startswith((".agentmesh/", self.worktrees.dirname + "/"))]

    # ---- main entry ----
    def run(self, task: Task, *, follow_up: str | None = None, allow_fallback: bool = True) -> WorkerResult:
        check_depth(self.config)
        prior = self.tasks.load_result(task.task_id) if follow_up else None
        task.delegation_depth = current_depth() + 1
        result = WorkerResult(task_id=task.task_id, role=task.role, worker=None, status="FAILED",
                              started_at=utcnow())
        try:
            cwd, owner, owns_base = self._prepare_workspace(task)
        except AgentMeshError as e:
            return self._finish(task, result, error=e.code, summary=str(e))
        result.worktree, result.branch = task.worktree, task.branch
        task.status = TaskStatus.RUNNING.value
        task.verified = False            # any new run (continue/correction) voids an earlier verification
        self.tasks.save(task)

        in_git = gitutil.repo_root(cwd) is not None and gitutil.has_commits(cwd)
        start_head = gitutil.head(cwd) if in_git else None
        pre_dirty = set(gitutil.dirty_files(cwd)) if in_git and owner is None else set()
        ref = (task.base_commit if owns_base else start_head)

        fb = self.config.get("fallback", {})
        max_attempts = int(fb.get("max_attempts", 3)) if fb.get("enabled", True) and allow_fallback else 1
        fallback_on = set(fb.get("on", []))
        role_md = read_role(self.config.paths.agents_dir, task.role)
        tried: set[str] = set()
        handover: str | None = None
        last_code: ErrorCode | None = None
        notes: list[str] = []
        raw_final = None

        bench_rows = load_saved()
        worker_attempts = 0
        authors_changed: list[str] = []
        authors = self.authors_of(task, authors_changed) if task.role in REVIEW_ROLES and self._needs_other_worker() else set()
        strict = bool(authors) and self._review_tier(task, authors_changed) == "high"
        while worker_attempts < max_attempts:
            ranking = self.router.rank(task.role, exclude=tried, preferred=task.preferred_worker,
                                       avoid=set(task.avoid_workers) | authors, strict_avoid=strict)
            if not ranking.candidates:
                if not result.attempts:
                    why = "; ".join(f"{w}: {r}" for w, r in ranking.skipped.items()) or "no workers registered"
                    return self._finish(task, result, error=ErrorCode.NO_WORKER_AVAILABLE,
                                        summary=f"no eligible worker for role '{task.role}' ({why})")
                break
            worker = ranking.candidates[0]
            tried.add(worker)
            adapter, info = self.registry.get(worker), self.infos[worker]
            prompt = build_prompt(task, role_md, self.config.name, follow_up=follow_up, prior=prior,
                                  fallback_note=handover, inputs=self._inputs(task))
            scratch = self.config.paths.runtime_dir / task.task_id
            scratch.mkdir(parents=True, exist_ok=True)
            ctx = RunContext(
                task=task, prompt=prompt, cwd=cwd, timeout=task.timeout_seconds,
                autonomy=self.config.role_autonomy(task.role),
                continue_session=bool(prior and prior.worker == worker and info.session_continue == "YES"),
                model=None,
                extra_args=list(self.config.get(f"workers.extra_args.{worker}", []) or []),
                scratch=scratch,
                env={DEPTH_ENV: str(task.delegation_depth), "AGENTMESH_TASK_ID": task.task_id,
                     "AGENTMESH_ROLE": task.role})
            self.emit(f"[{task.task_id}] role={task.role} -> worker={worker}")
            self.state.record_use(worker)
            models = self._model_chain(worker)
            worker_attempts += 1
            unsupported = False
            for mi, model in enumerate(models):
                ctx.model = model
                if len(models) > 1:
                    ctx.timeout = min(task.timeout_seconds, int(self.config.get("workers.model_timeout_seconds", 300)))
                try:
                    spec = adapter.build_command(ctx, info)
                    hb = Heartbeat(self.emit, f"[{task.task_id}] {worker}/{model or 'default'}", ctx.timeout,
                                   typical_seconds(worker, model, bench_rows),
                                   interval=float(self.config.get("progress.interval_seconds", 15)))
                    with hb:
                        raw = adapter.execute(spec, run_id=f"{task.task_id}-{worker}")
                    norm = adapter.normalize_result(raw)
                    code = adapter.classify_error(raw, norm)
                except UnsupportedError as e:
                    result.attempts.append(Attempt(worker, None, ErrorCode.UNSUPPORTED.value, 0.0, str(e)))
                    self.state.record_failure(worker, "UNSUPPORTED", self._cooldown("UNSUPPORTED"))
                    last_code, notes = ErrorCode.UNSUPPORTED, notes + [f"{worker}: {e}"]
                    unsupported = True
                    break
                raw_final = raw
                result.attempts.append(Attempt(worker, raw.exit_code, code.value if code else None, raw.duration,
                                               f"model={model}" if model else ""))
                result.worker, result.exit_code = worker, raw.exit_code
                result.stdout, result.stderr = redact(raw.stdout)[-20000:], redact(raw.stderr)[-8000:]
                result.summary, result.session_id = norm.summary, norm.session_id
                self._write_log(task, worker, len(result.attempts), result)
                evidence = (raw.stderr[-4000:] + "\n" + raw.stdout[-800:]) if code is not None else ""
                if code is not None and model and mi < len(models) - 1 and code in INFRA_ERRORS \
                        and code.value in fallback_on:
                    # this model is bad right now: remember it, try the worker's next model
                    self.state.record_failure(f"{worker}::{model}", code.value,
                                              self._cooldown(code.value, evidence) or 600)
                    self.emit(f"[{task.task_id}] {worker}/{model} -> {code.value}; trying next model")
                    continue
                break
            if unsupported:
                continue
            if code is None:
                self.state.record_success(worker)
                last_code = None
                break
            self.state.record_failure(worker, code.value, self._cooldown(code.value, evidence))
            last_code = code
            notes.append(f"{worker}: {code.value}")
            if code.value not in fallback_on or code not in INFRA_ERRORS:
                break
            handover = (f"Worker `{worker}` stopped with {code.value} before finishing. The workspace may contain its "
                        "partial changes: run `git status` / `git diff`, keep what is correct, and finish the task.")
            self.emit(f"[{task.task_id}] {worker} -> {code.value}; trying next worker")

        if last_code is None and result.attempts and result.attempts[-1].normalized_error is None:
            report = parse_report(result.summary)
            result.reported_files = [str(f) for f in report.get("files_changed", [])]
            result.tests = [t for t in report.get("tests", []) if isinstance(t, dict)]
            if report.get("summary"):
                result.summary = str(report["summary"])
            result.changed_files = self._changed(cwd, ref, pre_dirty)
            if result.worker in authors:
                note = f"NOTE: reviewed by {result.worker}, which also wrote the code (no other worker was available)."
                result.summary = f"{note} {result.summary}"
            viol = scope.violations(result.changed_files, task.allowed_paths, task.forbidden_paths,
                                    task.capability == "read-only")
            result.policy_violations = viol
            if report.get("status") in ("blocked", "failed"):
                return self._finish(task, result, error=ErrorCode.WORKER_FAILED,
                                    summary=f"worker reported {report['status']}: {result.summary}")
            if viol:
                result.status = "SCOPE_VIOLATION"
            else:
                result.status = "SUCCESS"
                if owner and self.config.get("worktree.auto_commit", True):
                    self.worktrees.checkpoint(owner, f"agentmesh: {task.task_id} {task.role} ({result.worker})")
            return self._finish(task, result)

        if last_code is None:      # attempts empty or exhausted without result
            last_code = ErrorCode.NO_WORKER_AVAILABLE
        result.changed_files = self._changed(cwd, ref, pre_dirty)
        summary = f"failed after {len(result.attempts)} attempt(s): " + "; ".join(notes or [last_code.value])
        return self._finish(task, result, error=last_code, summary=summary, keep_stdout=raw_final is not None)

    # ---- helpers ----
    def _needs_other_worker(self) -> bool:
        return bool(self.config.get("review.require_different_worker", True))

    def _review_tier(self, task: Task, changed: list[str]) -> str:
        """Stored tier, raised by the files the reviewed work really changed (same rule as `integrate`)."""
        from .risk import assess, higher
        found = assess("", changed, high_paths=self.config.get("risk.high_paths", []),
                       low_paths=self.config.get("risk.low_paths", []))
        return higher(task.risk, "high" if found.tier == "high" else task.risk)

    def authors_of(self, task: Task, changed: list[str] | None = None) -> set[str]:
        """Workers that wrote the code a review task looks at (via reuse/base/context tasks, transitively).
        Only tasks that changed files count: a spec or test run that wrote nothing is not an author.
        `changed` (if given) collects those files."""
        out: set[str] = set()
        seen: set[str] = set()
        todo = [*([task.reuse_worktree_of] if task.reuse_worktree_of else []), *task.base_tasks, *task.context_tasks]
        while todo:
            tid = todo.pop()
            if tid in seen:
                continue
            seen.add(tid)
            try:
                t = self.tasks.load(tid)
            except AgentMeshError:
                continue
            if t.role not in REVIEW_ROLES:
                r = self.tasks.load_result(tid)
                if r and r.changed_files:
                    out |= {a.worker for a in r.attempts} | ({r.worker} if r.worker else set())
                    if changed is not None:
                        changed.extend(r.changed_files)
            todo += [*([t.reuse_worktree_of] if t.reuse_worktree_of else []), *t.base_tasks, *t.context_tasks]
        return out

    def _model_chain(self, worker: str) -> list[str | None]:
        """Ordered models for a worker (workers.models.<worker>: str or list); known-bad ones are skipped."""
        cfg = self.config.get(f"workers.models.{worker}")
        models = [cfg] if isinstance(cfg, str) else [m for m in (cfg or []) if isinstance(m, str)]
        if not models:
            return [None]
        live = [m for m in models if self.state.cooldown_remaining(f"{worker}::{m}") == 0]
        return live or models

    def _cooldown(self, code: str, evidence: str = "") -> int:
        """Quota/rate limits: honour the reset time the provider printed; otherwise the configured default."""
        if code in ("QUOTA_EXCEEDED", "RATE_LIMITED") and (secs := parse_retry_seconds(evidence)):
            return secs
        return int(self.config.get("fallback.cooldown_seconds", {}).get(code, 0))

    def _write_log(self, task: Task, worker: str, n: int, result: WorkerResult) -> None:
        self.config.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.config.paths.logs_dir / f"{task.task_id}-{n}-{worker}.log").write_text(
            f"# stdout\n{result.stdout}\n\n# stderr\n{result.stderr}\n", encoding="utf-8")

    def _finish(self, task: Task, result: WorkerResult, *, error: ErrorCode | None = None,
                summary: str | None = None, keep_stdout: bool = True) -> WorkerResult:
        if error is not None:
            result.status = "FAILED"
            result.normalized_error = error.value
            if summary:
                result.summary = summary
        result.finished_at = utcnow()
        task.status = {"SUCCESS": TaskStatus.SUCCESS, "SCOPE_VIOLATION": TaskStatus.SCOPE_VIOLATION}.get(
            result.status, TaskStatus.FAILED).value
        self.tasks.save(task)
        self.tasks.save_result(result)
        return result
