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

from . import __version__, discovery, gitutil
from .adapters.base import RunContext
from .config import (ProjectConfig, ProjectPaths, deep_merge, dump_yaml, find_project_root, global_home, load_yaml,
                     parse_scalar, set_dotted, MACHINE_KEYS)
from .errors import AgentMeshError, ErrorCode
from .health import run_doctor
from .initializer import init_project, tty_asker
from .models import Task, TaskStatus, utcnow, WorkerResult
from .registry import Registry, build_registry
from .roles import GLOBAL_FORBIDDEN
from .runner import Runner, check_depth
from .state import RuntimeState
from .task_manager import TaskManager
from .workflow import plan_task

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
    plan = plan_task(wf, args.task, args.signal)
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
    if args.json:
        out(json.dumps(data, indent=2))
        return 0
    out(f"Task: {plan.task}\nSignals: {', '.join(plan.signals) or '-'}")
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
        roles = cfg.roles()
        if args.role not in roles:
            raise AgentMeshError(f"role '{args.role}' is not part of this project. Available: {', '.join(roles)}")
        spec = roles[args.role]
        desc = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else (args.description or "")
        title = args.title or desc.strip().splitlines()[0][:80] if (args.title or desc.strip()) else args.role
        read_only = spec.get("capability") == "read-only"
        task = Task(
            task_id=tm.next_id(), title=title, role=args.role, description=desc, capability=spec.get("capability", "write"),
            requirements=args.requirement or [], constraints=args.constraint or [], acceptance_criteria=args.acceptance or [],
            verification=args.verify or [f"{v['run']}  (in {v['cwd']})" for v in cfg.get("verification.commands", [])][:6],
            project_root=str(cfg.paths.root),
            allowed_paths=(spec.get("allowed_paths", []) + (args.allow or [])),
            forbidden_paths=spec.get("forbidden_paths", GLOBAL_FORBIDDEN),
            preferred_worker=args.worker, avoid_workers=args.avoid_worker or [],
            parent_task=args.parent, reuse_worktree_of=args.reuse_worktree,
            isolation="inplace" if (args.inplace or (read_only and not args.reuse_worktree)) else spec.get("isolation", "worktree"),
            timeout_seconds=args.timeout or int(cfg.get("workers.timeout_seconds", 1800)))
        tm.save(task)
        result = runner.run(task, allow_fallback=not args.no_fallback)

    if args.json:
        out(json.dumps(_result_view(result, args.verbose), indent=2))
    else:
        _print_result(result)
    return 0 if result.status == "SUCCESS" else EXIT_SCOPE if result.status == "SCOPE_VIOLATION" else EXIT_FAILED


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _project(args)
    registry = build_registry()
    rep = discovery.current(registry, refresh=args.refresh)
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    tasks = TaskManager(cfg.paths).list()
    workers = [{"worker": a.name, "state": a.state, "runtime": state.status(a.name), "cooldown_s": state.cooldown_remaining(a.name),
                "uses": state.uses(a.name)} for a in rep.agents if a.installed]
    if args.json:
        out(json.dumps({"project": cfg.name, "manager": cfg.get("manager.default"), "fallback": cfg.get("fallback.enabled"),
                        "workers": workers, "tasks": [{"task_id": t.task_id, "role": t.role, "status": t.status, "title": t.title,
                                                       "verified": t.verified, "branch": t.branch, "worktree": t.worktree} for t in tasks]}, indent=2))
        return 0
    out(f"AgentMesh — {cfg.name}\n\nManager: {cfg.get('manager.default')}   Strategy: {cfg.get('routing.strategy')}   "
        f"Fallback: {'ENABLED' if cfg.get('fallback.enabled') else 'DISABLED'}\n\nWorkers:")
    for w in workers:
        extra = f" ({w['cooldown_s']}s)" if w["cooldown_s"] else ""
        out(f"  {w['worker']:<14}{w['state']:<24}{w['runtime']}{extra}")
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
    from .worktree import Worktrees
    wt = Worktrees(cfg.paths.root, cfg.get("worktree.dir"))
    if task.verified or args.force:
        wt.checkpoint(task.task_id, f"agentmesh: {task.task_id} checkpoint before integrate")
    sha = wt.integrate(task.task_id)
    task.status = TaskStatus.INTEGRATED.value
    tm.save(task)
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
                         timeout=args.timeout, autonomy="edit", scratch=Path(tmp))
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
    sp.add_argument("--manager", help="manager CLI (claude, codex, antigravity, qwen, ...)")
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
    sp.add_argument("--json", action="store_true")
    sp = add("delegate", cmd_delegate, "run one role as a task on a routed worker (with fallback)")
    sp.add_argument("--role"); sp.add_argument("--title"); sp.add_argument("--description"); sp.add_argument("--description-file")
    sp.add_argument("--requirement", action="append"); sp.add_argument("--constraint", action="append")
    sp.add_argument("--acceptance", action="append"); sp.add_argument("--verify", action="append")
    sp.add_argument("--allow", action="append", help="extra allowed path glob for this task")
    sp.add_argument("--worker", help="preferred worker (fallback still applies)"); sp.add_argument("--avoid-worker", action="append")
    sp.add_argument("--strategy", choices=["balanced", "quality-first", "free-first", "preferred-order"])
    sp.add_argument("--reuse-worktree", metavar="TASK_ID", help="run inside another task's worktree (test/review)")
    sp.add_argument("--parent", metavar="TASK_ID"); sp.add_argument("--inplace", action="store_true")
    sp.add_argument("--continue", dest="continue_task", metavar="TASK_ID", help="focused correction in the same worktree")
    sp.add_argument("--message", help="correction instructions for --continue")
    sp.add_argument("--timeout", type=int); sp.add_argument("--no-fallback", action="store_true")
    sp.add_argument("--force", action="store_true"); sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--verbose", action="store_true")
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
    sp = add("smoke", cmd_smoke, "LIVE one-prompt check of a worker (may use quota)")
    sp.add_argument("worker"); sp.add_argument("--yes", action="store_true"); sp.add_argument("--timeout", type=int, default=180)
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
