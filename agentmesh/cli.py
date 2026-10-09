"""AgentMesh command line."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__, discovery, enforcement, gitutil
from .adapters.base import RunContext
from .config import (ProjectConfig, ProjectPaths, deep_merge, dump_yaml, find_project_root, global_home, load_yaml,
                     parse_scalar, set_dotted, MACHINE_KEYS)
from .batch import load_batch, run_batch, template_from_plan
from .delegation import TaskSpec, build_task
from .errors import AgentMeshError, ErrorCode
from .health import run_doctor
from .initializer import init_project, tty_asker
from .models import Task, TaskStatus, utcnow, WorkerResult
from .registry import Registry, build_registry
from .roles import GLOBAL_FORBIDDEN
from .runner import Runner, check_depth
from .state import RuntimeState
from .task_manager import TaskManager
from . import risk as risk_mod
from .workflow import REVIEW_ROLES, plan_task

EXIT_FAILED, EXIT_SCOPE = 3, 4


def out(msg: str = "") -> None:
    print(msg)


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def table(rows: list[list[str]], header: list[str] | None = None) -> str:
    rows = ([header] if header else []) + rows
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    if header:
        lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _project(args: argparse.Namespace, need: bool = True) -> ProjectConfig | None:
    start = Path(getattr(args, "path", None) or ".").resolve()
    root = find_project_root(start)
    if root is None:
        if need:
            raise AgentMeshError("not inside an AgentMesh project. Run `agentmesh init --auto` in the repository root.",
                                 ErrorCode.PROJECT_ERROR)
        return None
    return ProjectConfig.load(root)


def _infos(registry: Registry, refresh: bool = False) -> dict[str, Any]:
    return discovery.current(registry, refresh=refresh).by_name()


# ------------------------------------------------------------------ commands

def cmd_version(args: argparse.Namespace) -> int:
    out(f"agentmesh {__version__}")
    return 0


def _print_agent(a: Any) -> None:
    yn = lambda v: v
    out(f"{a.display_name}")
    out(f"  Installed:          {'YES' if a.installed else 'NO'}" + (f"  ({a.path})" if a.path else ""))
    if not a.installed:
        out(f"  Status:             {a.state}")
        out()
        return
    out(f"  Version:            {a.version or 'unknown'}")
    out(f"  Headless:           {yn(a.headless)}")
    out(f"  Structured output:  {a.structured_output}" + (f" ({', '.join(a.output_formats)})" if a.output_formats else ""))
    out(f"  Session continue:   {a.session_continue}")
    out(f"  Working directory:  {a.cwd_behavior}")
    out(f"  Native timeout:     {a.native_timeout}")
    out(f"  Auth:               {a.auth}")
    out(f"  Live smoke test:    {a.smoke}")
    out(f"  Status:             {a.state}")
    for n in a.notes:
        out(f"  note: {n}")
    out()


def cmd_discover(args: argparse.Namespace) -> int:
    registry = build_registry()
    cfg = _project(args, need=False)
    extra = tuple(cfg.get("discovery.extra_candidates", [])) if cfg else ()
    rep = discovery.discover(registry, extra_candidates=extra)
    old = discovery.load_cache()
    if old:     # keep user-triggered smoke results across refreshes
        prev = old.by_name()
        for a in rep.agents:
            if a.name in prev and prev[a.name].path == a.path and prev[a.name].version == a.version:
                a.smoke = prev[a.name].smoke
    discovery.save_cache(rep)
    if args.json:
        out(json.dumps(rep.to_dict(), indent=2))
        return 0
    out("Detected Coding Agents\n")
    for a in rep.agents:
        if a.installed:
            _print_agent(a)
    missing = [a.display_name for a in rep.agents if not a.installed]
    if missing:
        out("Not installed: " + ", ".join(missing))
    for u in rep.unknown:
        out(f"\nUnknown compatible CLI: {u.name}\n  Detected: {u.path}\n  Adapter: REQUIRED (see `agentmesh configure add-agent`)")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    registry = build_registry()
    rep = discovery.current(registry, refresh=args.refresh)
    cfg = _project(args, need=False)
    state = RuntimeState(cfg.paths.state_dir / "workers.json") if cfg else None
    if args.json:
        out(json.dumps(rep.to_dict(), indent=2))
        return 0
    rows = []
    for a in rep.agents:
        rt = state.status(a.name) if state and a.installed else "-"
        rows.append([a.name, "yes" if a.installed else "no", a.state, a.headless, a.structured_output, rt, a.version or "-"])
    out(table(rows, ["WORKER", "INSTALLED", "STATE", "HEADLESS", "STRUCTURED", "RUNTIME", "VERSION"]))
    out("\nRUNTIME is observed from past runs in this project. Remaining quota is not exposed by these CLIs and is never guessed.")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from .project_analyzer import analyze
    p = analyze(Path(args.path or ".").resolve())
    if args.json:
        out(json.dumps(p.to_dict(), indent=2))
        return 0
    out(f"Project: {p.name}\nKind:    {p.kind}\nGit:     {'yes' if p.is_git else 'no'}")
    out("\nComponents:")
    for c in p.components:
        extra = ", ".join(f"{k}={v}" for k, v in c.details.items() if k in ("state_management", "routing", "package_manager") and v)
        out(f"  - {c.path}  [{c.type}/{c.stack}] {', '.join(c.frameworks)} {extra}".rstrip())
    for label, val in (("Docs", p.docs), ("CI", p.ci), ("Tests", p.test_dirs), ("Contracts", p.contracts),
                       ("Migrations", p.migrations), ("Localization", p.localization), ("Workspace", p.workspace_markers)):
        if val:
            out(f"{label}: {', '.join(val)}")
    if p.sensitive:
        out("Sensitive: " + "; ".join(f"{g}: {', '.join(v[:3])}" for g, v in p.sensitive.items()))
    from .roles import select_roles
    out("Roles:   " + ", ".join(select_roles(p)))
    for q in p.questions:
        out(f"? would ask: {q.text}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    registry = build_registry()
    root = Path(args.path or ".").resolve()
    interactive = sys.stdin.isatty() and not args.yes
    answers = dict(kv.split("=", 1) for kv in args.answer or [])
    security = True if args.security else False if args.no_security else None
    rep = init_project(root, registry, asker=tty_asker if interactive else None, answers=answers,
                       manager=args.manager, reference=args.reference, dry_run=args.dry_run, security=security,
                       report_line=out)
    p = rep.profile
    out(f"AgentMesh init{' (dry run)' if args.dry_run else ''}: {p.name}")
    out(f"  kind:     {p.kind}   components: {', '.join(f'{c.path}[{c.stack}]' for c in p.components) or 'none detected'}")
    out(f"  manager:  {rep.manager}")
    out(f"  roles:    {', '.join(rep.roles)}")
    out(f"  workers:  {', '.join(rep.ready_workers) or 'none READY'}")
    out("\nFiles:")
    for path, status in rep.actions.items():
        out(f"  {status:<10} {path}")
    if rep.stale_roles:
        out(f"\nStale generated roles (no longer selected, left in place): {', '.join(rep.stale_roles)}")
    kept = [k for k, v in rep.actions.items() if v == "kept"]
    if kept:
        out("Human-edited files were kept as-is: " + ", ".join(kept))
    for a in rep.assumptions:
        out(f"assumption: {a}")
    for w in rep.warnings:
        out(f"warning: {w}")
    from . import benchmark as _bm
    for m in _bm.stale_messages(discovery.current(build_registry()).by_name()):
        out(f"benchmark: {m}")
    if not args.dry_run:
        out(f"\nNext: review .agentmesh/, commit it, then run `agentmesh launch {rep.manager}` (or just `{rep.manager}`).")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    registry = build_registry()
    cfg = _project(args, need=False)
    rep = discovery.current(registry, refresh=args.refresh)
    sections, overall = run_doctor(cfg.paths.root if cfg else None, registry, rep)
    if args.json:
        out(json.dumps({"overall": overall, "sections": [{"title": s.title, "checks": [c.__dict__ for c in s.checks]} for s in sections]}, indent=2))
    else:
        out("AgentMesh Doctor\n")
        for s in sections:
            out(s.title + "\n" + "-" * len(s.title))
            for c in s.checks:
                out(f"{c.label:<28}{c.status:<8}{c.detail}".rstrip())
            out()
        out("Overall\n-------\n" + overall)
    return 0 if overall != "NOT READY" else 1


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _project(args)
    wf = load_yaml(cfg.paths.workflow_yaml)
    from . import risk as risk_mod
    rk = risk_mod.assess(args.task, [], override=args.risk, vocab=wf.get("signals"),
                         high_paths=cfg.get("risk.high_paths", []), low_paths=cfg.get("risk.low_paths", []))
    plan = plan_task(wf, args.task, args.signal, risk=rk.tier, risk_reasons=rk.reasons,
                     review_mode=cfg.get("review.mode", "auto"))
    registry = build_registry()
    router_info = _infos(registry)
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    from .router import Router
    router = Router(registry, router_info, cfg, state)
    data = plan.to_dict()
    for s in data["stages"]:
        if s["role"]:
            rk = router.rank(s["role"])
            s["worker_preview"] = rk.candidates[:3]
    if args.emit_batch:
        if plan.needs_clarification:
            raise AgentMeshError("cannot emit a batch: " + plan.needs_clarification)
        Path(args.emit_batch).write_text(json.dumps(template_from_plan(data, args.task), indent=2), encoding="utf-8")
        err(f"wrote {args.emit_batch}: edit descriptions/acceptance, then `agentmesh run-batch {args.emit_batch}`")
    if args.json:
        out(json.dumps(data, indent=2))
        return 0
    out(f"Task: {plan.task}\nSignals: {', '.join(plan.signals) or '-'}\nRisk: {plan.risk} ({'; '.join(plan.risk_reasons) or '-'})")
    if plan.needs_clarification:
        out(f"NEEDS CLARIFICATION: {plan.needs_clarification}")
    for i, s in enumerate(data["stages"], 1):
        who = s["role"] or "manager"
        pre = ", ".join(s.get("worker_preview", []))
        out(f"  {i}. {s['id']:<20} {who:<20} {'[if needed] ' if s['conditional'] else ''}{('workers: ' + pre) if pre else ''}  # {s['reason']}")
    out("\nWorker preview is a snapshot; the router decides at delegation time.")
    return 0


def _result_view(r: WorkerResult, verbose: bool = False) -> dict[str, Any]:
    d = r.to_dict()
    if not verbose:
        d.pop("stdout"), d.pop("stderr")
    return d


def _print_result(r: WorkerResult) -> None:
    out(f"{r.task_id}  role={r.role}  worker={r.worker or '-'}  status={r.status}"
        + (f"  error={r.normalized_error}" if r.normalized_error else ""))
    for a in r.attempts:
        out(f"  attempt: {a.worker:<14} exit={a.exit_code}  error={a.normalized_error or '-'}  {a.duration_seconds}s")
    if r.worktree:
        out(f"  worktree: {r.worktree}\n  branch:   {r.branch}")
    out(f"  changed (from git): {', '.join(r.changed_files) or '(none)'}")
    if set(r.reported_files) != set(r.changed_files) and r.status == "SUCCESS":
        out(f"  note: worker claimed {sorted(r.reported_files)}; git shows {sorted(r.changed_files)}")
    for v in r.policy_violations:
        out(f"  VIOLATION: {v}")
    out(f"  summary: {r.summary[:600]}")


def cmd_delegate(args: argparse.Namespace) -> int:
    cfg = _project(args)
    registry = build_registry()
    infos = _infos(registry, args.refresh)
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    tm = TaskManager(cfg.paths)
    check_depth(cfg)
    runner = Runner(cfg, registry, infos, state, tm, on_event=(lambda m: err(m)) if not args.json else err)
    if args.strategy:
        cfg.data["routing"]["strategy"] = args.strategy

    if args.continue_task:
        task = tm.load(args.continue_task)
        rounds = int(load_yaml(cfg.paths.workflow_yaml).get("limits", {}).get("max_correction_rounds", 2))
        if task.retry_count >= rounds and not args.force:
            raise AgentMeshError(f"{task.task_id} already had {task.retry_count} correction rounds (limit {rounds}). "
                                 "Escalate to the user or pass --force.")
        if not args.message:
            raise AgentMeshError("--continue needs --message describing the focused correction")
        task.retry_count += 1
        if args.worker:
            task.preferred_worker = args.worker
        result = runner.run(task, follow_up=args.message, allow_fallback=not args.no_fallback)
    else:
        if not args.role:
            raise AgentMeshError("--role is required (see .agentmesh/agents/)")
        desc = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else (args.description or "")
        task = build_task(cfg, tm, TaskSpec(
            role=args.role, title=args.title or "", description=desc, requirements=args.requirement or [],
            constraints=args.constraint or [], acceptance=args.acceptance or [], verify=args.verify or [],
            allow=args.allow or [], worker=args.worker, avoid_workers=args.avoid_worker or [], parent=args.parent, risk=args.risk,
            reuse_worktree=args.reuse_worktree, base_tasks=args.base_task or [], inplace=args.inplace, timeout=args.timeout))
        result = runner.run(task, allow_fallback=not args.no_fallback)

    if args.json:
        out(json.dumps(_result_view(result, args.verbose), indent=2))
    else:
        _print_result(result)
    return 0 if result.status == "SUCCESS" else EXIT_SCOPE if result.status == "SCOPE_VIOLATION" else EXIT_FAILED


def cmd_run_batch(args: argparse.Namespace) -> int:
    cfg = _project(args)
    check_depth(cfg)
    registry = build_registry()
    infos = _infos(registry, args.refresh)
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    runner = Runner(cfg, registry, infos, state, TaskManager(cfg.paths), on_event=err)
    items = load_batch(Path(args.file))
    n = args.max_parallel or int(cfg.get("parallel.max_workers", 3))
    outcomes = run_batch(runner, cfg, items, max_parallel=n, fail_fast=args.fail_fast, emit=err)
    ok = all(o.status == "SUCCESS" for o in outcomes.values())
    if args.json:
        out(json.dumps({"ok": ok, "max_parallel": n, "tasks": {k: {
            "task_id": o.task_id, "status": o.status, "note": o.note,
            "result": _result_view(o.result) if o.result else None} for k, o in outcomes.items()}}, indent=2))
    else:
        out(table([[k, o.task_id or "-", items[[i.id for i in items].index(k)].spec.role, o.status,
                    (o.result.worker if o.result and o.result.worker else "-"), o.note[:60]] for k, o in outcomes.items()],
                  ["ID", "TASK", "ROLE", "STATUS", "WORKER", "NOTE"]))
        out("\nALL SUCCESS - inspect with `agentmesh diff`, then `agentmesh verify` each implementation task." if ok
            else "\nSome tasks did not succeed; fix with `agentmesh delegate --continue <task>` or re-plan.")
    return 0 if ok else EXIT_FAILED


def cmd_hook(args: argparse.Namespace) -> int:
    """Claude Code hook entry point. Reads the hook JSON on stdin. Fails OPEN on any internal error."""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        start = Path(payload.get("cwd") or ".")
        if args.event == "pre-edit":
            ti = payload.get("tool_input") or {}
            target = ti.get("file_path") or ti.get("notebook_path")
            if not target:
                return 0
            tp = Path(target) if Path(target).is_absolute() else start / target
            root = find_project_root(tp.parent if tp.parent.exists() else start)
            if root is None:
                return 0
            d = enforcement.decide_edit(ProjectConfig.load(root), str(tp))
            if d.message:
                err(d.message)
            return 0 if d.allow else 2
        root = find_project_root(start)
        if root is None:
            return 0
        cfg = ProjectConfig.load(root)
        if args.event == "stop":
            if payload.get("stop_hook_active"):          # already pushed back once: never trap the session in a loop
                return 0
            msg = enforcement.stop_message(cfg)
            if msg:
                err(msg)
                return 2
            return 0
        if args.event == "session-start":
            if enforcement.mode(cfg) != "off" and enforcement.current_depth() == 0:
                out(enforcement.session_summary(cfg))
            return 0
    except Exception as e:                               # a broken hook must never block the user's work
        err(f"agentmesh hook: ignored internal error: {e}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = _project(args)
    bad = enforcement.audit(cfg)
    if args.json:
        out(json.dumps({"direct_changes": bad}, indent=2))
    elif bad:
        out("Uncommitted changes in the main tree that did not come through a task branch:")
        for f in bad:
            out(f"  {f}")
        out("Delegate this work (`agentmesh delegate ...`) or revert it, so it is planned, scoped and verified.")
    else:
        out("clean: no direct product-code changes in the main tree")
    return 1 if bad else 0


def cmd_abandon(args: argparse.Namespace) -> int:
    cfg = _project(args)
    tm = TaskManager(cfg.paths)
    task = tm.load(args.task_id)
    task.status = TaskStatus.ABANDONED.value
    tm.save(task)
    out(f"{task.task_id} abandoned (worktree kept until `agentmesh clean --task {task.task_id} --yes`)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _project(args)
    registry = build_registry()
    rep = discovery.current(registry, refresh=args.refresh)
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    tasks = TaskManager(cfg.paths).list()
    workers = [{"worker": a.name, "state": a.state, "runtime": state.status(a.name), "cooldown_s": state.cooldown_remaining(a.name),
                "uses": state.uses(a.name)} for a in rep.agents if a.installed]
    cooling = [{"target": k, "reason": state.last_error(k), "seconds_left": v} for k, v in sorted(state.cooling().items())]
    if args.json:
        out(json.dumps({"project": cfg.name, "manager": cfg.get("manager.default"), "fallback": cfg.get("fallback.enabled"),
                        "workers": workers, "cooling_down": cooling, "tasks": [{"task_id": t.task_id, "role": t.role, "status": t.status, "title": t.title,
                                                       "verified": t.verified, "branch": t.branch, "worktree": t.worktree} for t in tasks]}, indent=2))
        return 0
    out(f"AgentMesh — {cfg.name}\n\nManager: {cfg.get('manager.default')}   Strategy: {cfg.get('routing.strategy')}   "
        f"Fallback: {'ENABLED' if cfg.get('fallback.enabled') else 'DISABLED'}\n\nWorkers:")
    for w in workers:
        extra = f" ({w['cooldown_s']}s)" if w["cooldown_s"] else ""
        out(f"  {w['worker']:<14}{w['state']:<24}{w['runtime']}{extra}")
    if cooling:
        out("\nCooling down (returns automatically when the timer ends):")
        for c in cooling:
            m, rem = divmod(c["seconds_left"], 60)
            out(f"  {c['target']:<58}{c['reason']:<18}{m // 60}h{m % 60:02d}m" if m >= 60 else
                f"  {c['target']:<58}{c['reason']:<18}{m}m{rem:02d}s")
    out("\nTasks:" + ("" if tasks else " none"))
    for t in tasks:
        out(f"  {t.task_id}  {t.status:<16}{'verified ' if t.verified else '          '}{t.role:<18}{t.title[:50]}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    cfg = _project(args)
    task = TaskManager(cfg.paths).load(args.task_id)
    if not task.worktree or not Path(task.worktree).is_dir() or not task.base_commit:
        raise AgentMeshError(f"{task.task_id} has no worktree diff (inplace/read-only task or worktree cleaned)")
    from .worktree import Worktrees
    wt = Worktrees(cfg.paths.root, cfg.get("worktree.dir"))
    owner = task.reuse_worktree_of or task.task_id
    out(wt.diff(owner, task.base_commit, stat=args.stat))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Run the project's verification commands in the task workspace and record the outcome."""
    cfg = _project(args)
    tm = TaskManager(cfg.paths)
    task = tm.load(args.task_id)
    cwd = Path(task.worktree) if task.worktree and Path(task.worktree).is_dir() else cfg.paths.root
    if args.accept:
        task.verified = True
        tm.save(task)
        out(f"{task.task_id} marked verified (manual acceptance: {args.accept})")
        return 0
    result = tm.load_result(task.task_id)
    changed = result.changed_files if result else []
    if not args.cmd and changed and result and result.status == "SUCCESS" and not result.policy_violations \
            and effective_risk(cfg, task, changed) == "low" and risk_mod.assess("", changed).tier == "low":
        task.verified = True            # low tier: docs/text only and scope clean, so the diff check is the verification
        tm.save(task)
        out(f"{task.task_id}: VERIFIED (low risk, diff check only: {', '.join(changed[:5])})")
        return 0
    cmds = [{"name": "custom", "cwd": ".", "run": c} for c in args.cmd] if args.cmd else cfg.get("verification.commands", [])
    if not args.cmd and changed:
        touched = [c for c in cmds if c["component"] == "." or any(f.startswith(c["component"] + "/") for f in changed)]
        cmds = touched or cmds
    if not cmds:
        out("no verification commands configured; review manually, then `agentmesh verify <id> --accept 'reason'`")
        return 1
    results, ok_all = [], True
    for c in cmds:
        run_dir = cwd / c["cwd"]
        argv = shlex.split(c["run"])
        if not shutil.which(argv[0]):
            results.append({**c, "result": "NOT_RUN", "detail": f"{argv[0]} not found"})
            ok_all = False
            continue
        try:
            r = subprocess.run(argv, cwd=run_dir, capture_output=True, text=True, timeout=args.timeout, errors="replace")
            results.append({**c, "result": "PASS" if r.returncode == 0 else "FAIL", "exit": r.returncode,
                            "tail": (r.stdout + r.stderr)[-1500:] if r.returncode else ""})
            ok_all &= r.returncode == 0
        except subprocess.TimeoutExpired:
            results.append({**c, "result": "TIMEOUT"})
            ok_all = False
    task.verified = ok_all
    tm.save(task)
    cfg.paths.reports_dir.mkdir(parents=True, exist_ok=True)
    from .security import redact
    (cfg.paths.reports_dir / f"{task.task_id}.verify.json").write_text(redact(json.dumps(results, indent=2)), encoding="utf-8")
    if args.json:
        out(redact(json.dumps({"task_id": task.task_id, "verified": ok_all, "results": results}, indent=2)))
    else:
        for r in results:
            out(f"  {r['result']:<8}{r['run']}   (in {r['cwd']})")
            if r.get("tail"):
                out("    " + redact(r["tail"]).replace("\n", "\n    ")[-900:])
        out(f"{task.task_id}: {'VERIFIED' if ok_all else 'NOT VERIFIED'}")
    return 0 if ok_all else 1


def effective_risk(cfg: ProjectConfig, task: Any, changed: list[str]) -> str:
    """Stored tier, raised to high when the files actually changed say so (text can understate)."""
    found = risk_mod.assess("", changed, high_paths=cfg.get("risk.high_paths", []), low_paths=cfg.get("risk.low_paths", []))
    return risk_mod.higher(task.risk, "high" if found.tier == "high" else task.risk)


def _review_passed(tm: TaskManager, task: Any) -> bool:
    for t in tm.list():
        if t.role in REVIEW_ROLES and task.task_id in (t.reuse_worktree_of, *t.base_tasks, *t.context_tasks):
            r = tm.load_result(t.task_id)
            if r and r.status == "SUCCESS":
                return True
    return False


def cmd_integrate(args: argparse.Namespace) -> int:
    cfg = _project(args)
    tm = TaskManager(cfg.paths)
    task = tm.load(args.task_id)
    if task.reuse_worktree_of or not task.branch:
        raise AgentMeshError(f"{task.task_id} owns no branch; integrate the implementing task instead")
    if task.status != TaskStatus.SUCCESS.value and not args.force:
        raise AgentMeshError(f"{task.task_id} status is {task.status}; only SUCCESS tasks can be integrated")
    if not task.verified and not args.force:
        raise AgentMeshError(f"{task.task_id} is not verified. Run `agentmesh verify {task.task_id}` (or --force).")
    if not args.force and cfg.get("review.mode", "auto") != "never" and not _review_passed(tm, task):
        res = tm.load_result(task.task_id)
        if effective_risk(cfg, task, res.changed_files if res else []) == "high" or cfg.get("review.mode") == "always":
            raise AgentMeshError(f"{task.task_id} is high risk: a successful reviewer task is required before integrate "
                                 f"(delegate --role reviewer --reuse-worktree {task.task_id} --avoid-worker <author>, or --force)")
    from .worktree import Worktrees
    wt = Worktrees(cfg.paths.root, cfg.get("worktree.dir"))
    if task.verified or args.force:
        wt.checkpoint(task.task_id, f"agentmesh: {task.task_id} checkpoint before integrate")
    sha = wt.integrate(task.task_id)
    task.status = TaskStatus.INTEGRATED.value
    tm.save(task)
    for other in tm.list():          # base tasks now contained in main are integrated too
        if other.task_id != task.task_id and other.branch and other.status == TaskStatus.SUCCESS.value and \
                gitutil.git(cfg.paths.root, "merge-base", "--is-ancestor", other.branch, "HEAD", check=False).returncode == 0:
            other.status = TaskStatus.INTEGRATED.value
            tm.save(other)
            out(f"{other.task_id} was part of {task.task_id}: marked INTEGRATED")
    if args.cleanup:
        wt.remove(task.task_id, delete_branch=True)
    out(f"{task.task_id} merged into {gitutil.current_branch(cfg.paths.root)} at {sha[:10]}")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    cfg = _project(args)
    registry = build_registry()
    name = registry.resolve(args.manager or cfg.get("manager.default"))
    if not registry.has(name):
        raise AgentMeshError(f"unknown manager CLI '{name}'")
    rep = discovery.current(registry, refresh=True)
    by = rep.by_name()
    info = by.get(name)
    if info is None or not info.installed:
        raise AgentMeshError(f"manager '{name}' is not installed")
    sections, overall = run_doctor(cfg.paths.root, registry, rep)
    errors = [c for s in sections for c in s.checks if c.status == "ERROR"]
    if errors and not args.force:
        for c in errors:
            err(f"error: {c.label}: {c.detail}")
        raise AgentMeshError("AgentMesh config invalid; fix the errors above or pass --force")
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    out(f"AgentMesh — {cfg.name}\n\nManager:\n  {info.display_name}\n\nWorkers:")
    for a in rep.agents:
        if not a.installed or a.name == name:
            continue
        rt = state.status(a.name)
        label = a.state if a.state != "READY" else ("READY" if rt == "OK" else rt)
        out(f"  {a.display_name:<24}{label}")
    out(f"\nFallback:\n  {'ENABLED' if cfg.get('fallback.enabled') else 'DISABLED'}   Strategy: {cfg.get('routing.strategy')}"
        f"\n\nLaunching {info.display_name}...\n")
    os.chdir(cfg.paths.root)
    env = {**os.environ, "AGENTMESH_DEPTH": "0", "AGENTMESH_MANAGER": name}
    sys.stdout.flush()
    os.execvpe(info.path or name, [info.path or name, *args.extra], env)
    return 0   # pragma: no cover


def cmd_clean(args: argparse.Namespace) -> int:
    cfg = _project(args)
    tm = TaskManager(cfg.paths)
    from .worktree import Worktrees
    wt = Worktrees(cfg.paths.root, cfg.get("worktree.dir"))
    plan: list[tuple[str, Any]] = []
    for t in tm.list():
        if args.task and t.task_id != args.task:
            continue
        if (t.worktree and Path(t.worktree).is_dir() and not t.reuse_worktree_of
                and (args.all or args.worktrees or args.task or t.status in (TaskStatus.INTEGRATED.value, TaskStatus.ABANDONED.value))):
            plan.append((f"worktree {t.worktree}", lambda t=t: wt.remove(t.task_id, delete_branch=t.status == TaskStatus.INTEGRATED.value)))
    if not args.task and (args.all or args.state or not args.worktrees):
        for d in (cfg.paths.runtime_dir, cfg.paths.logs_dir, cfg.paths.state_dir):
            if d.exists():
                plan.append((f"dir {d.relative_to(cfg.paths.root)}", lambda d=d: shutil.rmtree(d, ignore_errors=True)))
    if not plan:
        out("nothing to clean")
        return 0
    for label, _ in plan:
        out(("removing " if args.yes else "would remove ") + label)
    if not args.yes:
        out("\nre-run with --yes to apply. Task and report records are never deleted.")
        return 0
    for _, fn in plan:
        fn()
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    if args.action == "add-agent":
        if not args.name or not args.executable:
            raise AgentMeshError("usage: configure add-agent NAME --executable EXE [--headless-arg=ARG ...] [--stdin-prompt]")
        path = global_home() / "agents.yaml"
        data = load_yaml(path) if path.is_file() else {}
        spec: dict[str, Any] = {"executable": args.executable, "display_name": args.display_name or args.name}
        for key, val in (("headless_args", args.headless_arg), ("edit_args", args.edit_arg), ("full_args", args.full_arg),
                         ("continue_args", args.continue_arg)):
            if val:
                spec[key] = val
        if args.stdin_prompt:
            spec["stdin_prompt"] = True
        data.setdefault("agents", {})[args.name] = spec
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_yaml(data), encoding="utf-8")
        out(f"wrote {path}. Run `agentmesh discover` to refresh. "
            + ("" if spec.get("headless_args") or spec.get("stdin_prompt") else "No headless_args yet: worker stays CONFIGURATION_REQUIRED."))
        return 0
    cfg = _project(args)
    if args.action == "show" or args.action is None:
        out(dump_yaml(cfg.data))
    elif args.action == "get":
        out(json.dumps(cfg.get(args.name), indent=2))
    elif args.action == "set":
        if args.name is None or args.value is None:
            raise AgentMeshError("usage: configure set KEY VALUE   (VALUE is JSON or plain text)")
        head = args.name.split(".")[0]
        if head in MACHINE_KEYS:
            raise AgentMeshError(f"'{head}' is regenerated by init; use overrides.* (e.g. overrides.roles.<role>.allowed_paths)")
        raw = load_yaml(cfg.paths.project_yaml)
        set_dotted(raw, args.name, parse_scalar(args.value))
        cfg.paths.project_yaml.write_text(dump_yaml(raw), encoding="utf-8")
        out(f"set {args.name} = {args.value}")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """LIVE: measure each worker/model on the same coding tasks (hidden unit tests). Uses quota/credits."""
    from . import benchmark as bm
    registry = build_registry()
    rep = discovery.current(registry, refresh=True)
    infos = rep.by_name()
    cfg = _project(args, need=False)
    lists: dict[str, list[str]] = {}
    if cfg:
        lists = {w: list(m) for w, m in (cfg.get("workers.models") or {}).items() if isinstance(m, list)}
    from .adapters import antigravity as ag_mod, kilo as kilo_mod, kiro as kiro_mod, opencode as oc_mod
    if args.wide:                                    # every candidate model the CLIs report, not just the seeded chains
        if infos.get("kilo") and infos["kilo"].ready:
            code, txt = oc_mod.probe_command([infos["kilo"].path or "kilo", "models"], timeout=45)
            lists["kilo"] = [l.strip() for l in txt.splitlines() if l.strip().endswith(":free")]
        if infos.get("opencode") and infos["opencode"].ready:
            lists["opencode"] = list(dict.fromkeys([*lists.get("opencode", []), *oc_mod.EXTRA_CANDIDATES]))
        if infos.get("kiro") and infos["kiro"].ready:
            import json as _j
            code, txt = oc_mod.probe_command([infos["kiro"].path or "kiro-cli", "chat", "--list-models", "-f", "json"], timeout=45)
            try:
                lists["kiro"] = [m["model_id"] for m in _j.loads(txt[txt.index("{"):])["models"] if m["model_id"] != "claude-sonnet-4"]
            except (ValueError, KeyError):
                pass
    else:
        for name, mod in (("opencode", oc_mod), ("kilo", kilo_mod), ("kiro", kiro_mod), ("antigravity", ag_mod)):
            if name not in lists and infos.get(name) and infos[name].ready:
                lists[name] = mod.available_preferred_models(infos[name].path)
    only = {registry.resolve(w) for w in args.workers.split(",")} if args.workers else None
    cands = bm.candidates(registry, infos, lists, only)
    tasks = [t for t in bm.TASKS if not args.tasks or t.id in args.tasks.split(",")]
    runs = len(cands) * len(tasks)
    out(f"{len(cands)} candidates x {len(tasks)} tasks = up to {runs} live runs (parallel {args.parallel}, {args.per_worker} per CLI).")
    for w, m in cands:
        out(f"  {w}{'/' + m if m else ''}")
    if not args.yes:
        out("\nThis sends real prompts and may use paid quota/credits. Re-run with --yes to start.")
        return 1
    results = bm.run_benchmark(registry, infos, cands, tasks=tasks, parallel=args.parallel, per_worker=args.per_worker,
                               timeout=args.timeout, emit=err)
    path = bm.save(results, {w: i.version for w, i in infos.items()})
    rows = [[f"{i}", r.worker, r.model or "(default)", f"{r.total * 100:.0f}%", f"{r.seconds:.0f}s",
             " ".join(f"{s.score:.1f}" for s in r.scores), ",".join(r.errors)] for i, r in enumerate(bm.ranked(results), 1)]
    out("\n" + table(rows, ["#", "WORKER", "MODEL", "SCORE", "AVG TIME", "PER TASK", "ERRORS"]))
    sug = bm.suggestions(results)
    out(f"\nsaved {path}\nsuggested worker quality order: {', '.join(sug['quality_order']) or '-'}")
    if args.apply:
        if not cfg:
            raise AgentMeshError("--apply needs an initialized project")
        raw = load_yaml(cfg.paths.project_yaml)
        for w, chain in sug["model_chains"].items():
            set_dotted(raw, f"workers.models.{w}", chain)
        if sug["quality_order"]:
            set_dotted(raw, "routing.quality_order", sug["quality_order"])
        cfg.paths.project_yaml.write_text(dump_yaml(raw), encoding="utf-8")
        out("applied: workers.models.* chains (score >= 50%, best first) and routing.quality_order in project.yaml")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """Optional LIVE check: sends one tiny prompt (may consume quota) to confirm the invocation works."""
    registry = build_registry()
    name = registry.resolve(args.worker)
    adapter, rep = registry.get(name), discovery.current(registry)
    info = rep.by_name().get(name)
    if info is None or not info.ready:
        raise AgentMeshError(f"{name} is not READY; nothing to smoke-test")
    if not args.yes:
        out(f"This sends one short prompt to {info.display_name} and may use paid quota. Re-run with --yes to proceed.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        task = Task(task_id="smoke", title="smoke", role="smoke", capability="read-only")
        ctx = RunContext(task=task, prompt="Reply with the single word OK and do not use any tools.", cwd=Path(tmp),
                         timeout=args.timeout, autonomy=args.autonomy, scratch=Path(tmp), model=args.model)
        raw = adapter.execute(adapter.build_command(ctx, info), run_id="smoke")
        norm = adapter.normalize_result(raw)
        code = adapter.classify_error(raw, norm)
    ok = code is None and "ok" in norm.summary.lower()
    info.smoke = "PASSED" if ok else "FAILED"
    discovery.save_cache(rep)
    out(f"{name}: smoke {info.smoke}" + (f" ({code.value})" if code else "") + f"\n  reply: {norm.summary[:200]!r}")
    return 0 if ok else 1


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentmesh", description="Role-based orchestration over AI coding CLIs.")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, fn: Any, help: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help, description=help)
        sp.set_defaults(fn=fn)
        sp.add_argument("--path", help="project directory (default: current)")
        return sp

    add("version", cmd_version, "print version")
    sp = add("init", cmd_init, "analyze the project and generate .agentmesh/")
    sp.add_argument("--auto", action="store_true", help="analyze first; ask only unresolved questions (default behaviour)")
    sp.add_argument("--yes", "-y", action="store_true", help="never prompt; use defaults and record assumptions")
    sp.add_argument("--manager", help="manager CLI (claude, codex, antigravity, kiro, ...)")
    sp.add_argument("--reference", help="reference folder or git URL for role methodology")
    sp.add_argument("--answer", action="append", metavar="ID=VALUE", help="pre-answer a question (repeatable)")
    sp.add_argument("--security", action="store_true", help="force security-reviewer role")
    sp.add_argument("--no-security", action="store_true", help="drop security-related review roles")
    sp.add_argument("--dry-run", action="store_true")
    sp = add("discover", cmd_discover, "inspect this machine for coding CLIs (free: --version/--help only)")
    sp.add_argument("--json", action="store_true")
    sp = add("agents", cmd_agents, "list known workers and their cached state")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--refresh", action="store_true")
    sp = add("analyze", cmd_analyze, "analyze the repository (no writes)")
    sp.add_argument("--json", action="store_true")
    sp = add("doctor", cmd_doctor, "check project config, roles, workers, git (no model calls)")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--refresh", action="store_true")
    sp = add("plan", cmd_plan, "map a request to workflow stages and roles")
    sp.add_argument("task"); sp.add_argument("--signal", action="append", help="force a signal (api, app, backend, web, security, ...)")
    sp.add_argument("--risk", choices=["low", "normal", "high"], help="override the computed risk tier")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--emit-batch", metavar="FILE", help="also write a run-batch file for this plan")
    sp = add("delegate", cmd_delegate, "run one role as a task on a routed worker (with fallback)")
    sp.add_argument("--role"); sp.add_argument("--title"); sp.add_argument("--description"); sp.add_argument("--description-file")
    sp.add_argument("--requirement", action="append"); sp.add_argument("--constraint", action="append")
    sp.add_argument("--acceptance", action="append"); sp.add_argument("--verify", action="append")
    sp.add_argument("--allow", action="append", help="extra allowed path glob for this task")
    sp.add_argument("--worker", help="preferred worker (fallback still applies)"); sp.add_argument("--avoid-worker", action="append")
    sp.add_argument("--strategy", choices=["balanced", "quality-first", "free-first", "preferred-order"])
    sp.add_argument("--reuse-worktree", metavar="TASK_ID", help="run inside another task's worktree (test/review)")
    sp.add_argument("--base-task", action="append", metavar="TASK_ID",
                    help="build on this SUCCESS task's work (repeat to combine several, e.g. backend + app for testing)")
    sp.add_argument("--parent", metavar="TASK_ID"); sp.add_argument("--inplace", action="store_true")
    sp.add_argument("--continue", dest="continue_task", metavar="TASK_ID", help="focused correction in the same worktree")
    sp.add_argument("--message", help="correction instructions for --continue")
    sp.add_argument("--timeout", type=int); sp.add_argument("--no-fallback", action="store_true")
    sp.add_argument("--risk", choices=["low", "normal", "high"], help="override the computed risk tier")
    sp.add_argument("--force", action="store_true"); sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--verbose", action="store_true")
    sp = add("run-batch", cmd_run_batch, "run a batch file: independent tasks in parallel, dependent ones in order")
    sp.add_argument("file"); sp.add_argument("--max-parallel", type=int); sp.add_argument("--fail-fast", action="store_true")
    sp.add_argument("--refresh", action="store_true"); sp.add_argument("--json", action="store_true")
    sp = add("hook", cmd_hook, "Claude Code hook entry (installed by init): pre-edit | stop | session-start")
    sp.add_argument("event", choices=["pre-edit", "stop", "session-start"])
    sp = add("audit", cmd_audit, "list direct product-code changes in the main tree (any manager CLI)")
    sp.add_argument("--json", action="store_true")
    sp = add("abandon", cmd_abandon, "drop a task so it no longer blocks finishing"); sp.add_argument("task_id")
    sp = add("status", cmd_status, "workers and tasks")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--refresh", action="store_true")
    sp = add("diff", cmd_diff, "show a task's real git diff"); sp.add_argument("task_id"); sp.add_argument("--stat", action="store_true")
    sp = add("verify", cmd_verify, "run verification commands in a task workspace")
    sp.add_argument("task_id"); sp.add_argument("--cmd", action="append"); sp.add_argument("--timeout", type=int, default=900)
    sp.add_argument("--accept", metavar="REASON", help="mark verified after manual review"); sp.add_argument("--json", action="store_true")
    sp = add("integrate", cmd_integrate, "merge a verified task branch")
    sp.add_argument("task_id"); sp.add_argument("--force", action="store_true"); sp.add_argument("--cleanup", action="store_true")
    sp = add("launch", cmd_launch, "validate config, show worker status, start the manager CLI")
    sp.add_argument("manager", nargs="?"); sp.add_argument("--force", action="store_true")
    sp.add_argument("extra", nargs=argparse.REMAINDER, help="args after -- are passed to the manager CLI")
    sp = add("clean", cmd_clean, "remove worktrees/runtime state (never task or report records)")
    sp.add_argument("--worktrees", action="store_true"); sp.add_argument("--state", action="store_true")
    sp.add_argument("--all", action="store_true"); sp.add_argument("--task"); sp.add_argument("--yes", action="store_true")
    sp = add("configure", cmd_configure, "show/set project config or register a generic CLI")
    sp.add_argument("action", nargs="?", choices=["show", "get", "set", "add-agent"])
    sp.add_argument("name", nargs="?"); sp.add_argument("value", nargs="?")
    sp.add_argument("--executable"); sp.add_argument("--display-name")
    sp.add_argument("--headless-arg", action="append"); sp.add_argument("--edit-arg", action="append")
    sp.add_argument("--full-arg", action="append"); sp.add_argument("--continue-arg", action="append")
    sp.add_argument("--stdin-prompt", action="store_true")
    sp = add("benchmark", cmd_benchmark, "LIVE: score every worker/model on hidden-test coding tasks (uses quota)")
    sp.add_argument("--yes", action="store_true"); sp.add_argument("--apply", action="store_true", help="write measured model chains + quality_order into project.yaml")
    sp.add_argument("--wide", action="store_true", help="include every model each CLI lists (all kilo :free, all kiro, extra opencode)")
    sp.add_argument("--workers", help="comma list to limit, e.g. kilo,kiro"); sp.add_argument("--tasks", help="comma list of task ids: slugify,lru,intervals,duration")
    sp.add_argument("--parallel", type=int, default=6); sp.add_argument("--per-worker", type=int, default=2)
    sp.add_argument("--timeout", type=int, default=240)
    sp = add("smoke", cmd_smoke, "LIVE one-prompt check of a worker (may use quota)")
    sp.add_argument("worker"); sp.add_argument("--yes", action="store_true"); sp.add_argument("--timeout", type=int, default=180)
    sp.add_argument("--model", help="model for the check (also set workers.models.<worker> in project.yaml to use it in tasks)")
    sp.add_argument("--autonomy", choices=["edit", "full"], default="edit", help="full is needed for workers that refuse edit mode (cline); smoke runs in an empty temp dir")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    if args.command == "launch" and args.extra and args.extra[0] == "--":
        args.extra = args.extra[1:]
    try:
        return args.fn(args)
    except AgentMeshError as e:
        err(f"agentmesh: error{'' if e.code is ErrorCode.UNKNOWN_ERROR else ' [' + e.code.value + ']'}: {e}")
        return 1
    except KeyboardInterrupt:
        return 130
