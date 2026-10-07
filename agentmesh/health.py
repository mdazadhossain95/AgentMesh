"""`agentmesh doctor`: free checks only (no model calls)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import bootstrap, enforcement, gitutil
from .config import ProjectConfig, load_yaml
from .discovery import DiscoveryReport
from .errors import AgentMeshError
from .registry import Registry
from .router import Router
from .security import redact
from .state import RuntimeState


@dataclass
class Check:
    label: str
    status: str            # READY | WARN | ERROR | INFO
    detail: str = ""


@dataclass
class Section:
    title: str
    checks: list[Check] = field(default_factory=list)

    def add(self, label: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(label, status, detail))


def run_doctor(root: Path | None, registry: Registry, report: DiscoveryReport) -> tuple[list[Section], str]:
    sections: list[Section] = []
    cfg: ProjectConfig | None = None
    proj = Section("Project")
    if root is None:
        proj.add("AgentMesh project", "WARN", "not initialized here (run `agentmesh init --auto`)")
    else:
        try:
            cfg = ProjectConfig.load(root)
            proj.add(cfg.name, "READY", f"kind={cfg.kind}  manager={cfg.get('manager.default')}")
        except AgentMeshError as e:
            proj.add(str(root), "ERROR", str(e))
    sections.append(proj)

    if cfg is not None:
        pack = Section("Agent Pack")
        roles = cfg.roles()
        for r in roles:
            f = cfg.paths.agents_dir / f"{r}.md"
            pack.add(r, "READY" if f.is_file() else "ERROR", "" if f.is_file() else "role file missing (re-run init)")
        sections.append(pack)
        wf = Section("Workflow")
        if cfg.paths.workflow_yaml.is_file():
            try:
                data = load_yaml(cfg.paths.workflow_yaml)
                bad = [s["role"] for s in data.get("stages", []) if s.get("role") and s["role"] not in roles]
                wf.add("workflow.yaml", "ERROR" if bad else "READY", f"stages reference unknown roles: {bad}" if bad else f"{len(data.get('stages', []))} stages")
            except (AgentMeshError, KeyError) as e:
                wf.add("workflow.yaml", "ERROR", str(e))
        else:
            wf.add("workflow.yaml", "ERROR", "missing")
        sections.append(wf)

    workers = Section("Workers")
    by = report.by_name()
    state = RuntimeState(cfg.paths.state_dir / "workers.json") if cfg else None
    for a in registry.adapters():
        i = by.get(a.name)
        if i is None or not i.installed:
            workers.add(a.display_name, "INFO", "NOT_INSTALLED")
            continue
        rt = state.status(a.name) if state else "OK"
        status = "READY" if i.ready and rt in ("OK", "RECENT_FAILURE") else "WARN" if i.ready else "WARN"
        detail = (i.state if i.state != "READY" else "") + (f" / {rt}" if rt != "OK" else "") + \
            f"  v={i.version or 'unknown'}  auth={i.auth}  smoke={i.smoke}"
        detail = detail.strip()
        workers.add(a.display_name, status, detail)
    ready = [a for a in report.agents if a.ready]
    workers.add("Ready workers", "READY" if len(ready) >= 2 else "WARN" if ready else "ERROR",
                f"{len(ready)} ({', '.join(a.name for a in ready) or 'none'})" + ("" if len(ready) >= 2 else "  -> fallback needs >= 2"))
    sections.append(workers)

    if cfg is not None and state is not None:
        route = Section("Routing")
        router = Router(registry, by, cfg, state)
        for r, spec in cfg.roles().items():
            rk = router.rank(r)
            n = len(rk.candidates)
            route.add(r, "READY" if n >= 2 else "WARN" if n == 1 else "ERROR",
                      f"{n} eligible: {', '.join(rk.candidates) or 'none'}")
        route.add("strategy", "INFO", f"{cfg.get('routing.strategy')}  fallback={'ENABLED' if cfg.get('fallback.enabled') else 'DISABLED'}")
        sections.append(route)

    git = Section("Git")
    gr = gitutil.repo_root(root or Path.cwd())
    if gr is None:
        git.add("repository", "WARN" if root else "INFO", "not a git repository (worktree isolation unavailable)")
    else:
        git.add("repository", "READY", str(gr))
        git.add("commits", "READY" if gitutil.has_commits(gr) else "WARN", "" if gitutil.has_commits(gr) else "no commits: worktrees need one")
    sections.append(git)

    if cfg is not None:
        sec = Section("Security & hygiene")
        for f in cfg.get("manager.bootstrap_files", []):
            p = cfg.paths.root / f
            ok = p.is_file() and bootstrap.BEGIN in p.read_text(encoding="utf-8")
            sec.add(f, "READY" if ok else "WARN", "" if ok else "AgentMesh section missing")
        if cfg.get("enforcement.mode") != "off" and cfg.get("enforcement.claude_hooks", True):
            ok = enforcement.claude_hooks_installed(cfg.paths.root)
            sec.add("claude hooks", "READY" if ok else "WARN",
                    f"enforcement={cfg.get('enforcement.mode')}" if ok else "not installed in .claude/settings.json (re-run init)")
        gi = cfg.paths.root / ".gitignore"
        ok = gi.is_file() and ".agentmesh/runtime/" in gi.read_text(encoding="utf-8")
        sec.add(".gitignore", "READY" if ok else "WARN", "" if ok else "runtime/worktree ignore block missing")
        if cfg.get("workers.autonomy") == "full" or any(v == "full" for v in (cfg.get("workers.role_autonomy") or {}).values()):
            sec.add("autonomy", "WARN", "'full' disables worker permission prompts; workers run commands unsandboxed")
        leaked = [p.name for p in [cfg.paths.project_yaml, *cfg.paths.agents_dir.glob("*.md")]
                  if p.is_file() and redact(p.read_text(encoding="utf-8")) != p.read_text(encoding="utf-8")]
        sec.add("secrets in config", "ERROR" if leaked else "READY", f"secret-like strings in {leaked}" if leaked else "none found")
        sections.append(sec)

    errors = [c for s in sections for c in s.checks if c.status == "ERROR"]
    warns = [c for s in sections for c in s.checks if c.status == "WARN"]
    overall = "NOT READY" if errors else "READY (with warnings)" if warns else "READY"
    return sections, overall
