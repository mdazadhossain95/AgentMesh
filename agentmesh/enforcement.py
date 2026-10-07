"""Make the manager follow the AgentMesh loop instead of merely asking it to.

Claude Code gets real hooks (PreToolUse blocks direct product-code edits, Stop blocks finishing with unverified work,
SessionStart shows status). Other manager CLIs: instructions plus `agentmesh audit`, since no hook mechanism of theirs
has been verified here. Hooks apply only to the manager: workers launched by AgentMesh carry AGENTMESH_DEPTH >= 1.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import gitutil
from .config import ProjectConfig
from .models import TaskStatus
from .roles import select_roles  # noqa: F401  (kept for role hints)
from .runner import current_depth
from .scope import matches
from .task_manager import TaskManager

HOOK_PREFIX = "agentmesh hook"
DEFAULT_ALLOW = [".agentmesh/**", ".claude/**", ".gitignore", "CLAUDE.md", "AGENTS.md", "QWEN.md", "**/*.md", "docs/**"]
EDIT_TOOLS = "Edit|Write|MultiEdit|NotebookEdit"


@dataclass
class Decision:
    allow: bool
    message: str = ""


def mode(cfg: ProjectConfig) -> str:
    if os.environ.get("AGENTMESH_ENFORCE", "").lower() == "off":
        return "off"
    return str(cfg.get("enforcement.mode", "block"))


def suggest_role(cfg: ProjectConfig, rel: str) -> str | None:
    for role, spec in cfg.roles().items():
        if spec.get("capability") == "write" and matches(rel, spec.get("allowed_paths", [])):
            return role
    return None


def decide_edit(cfg: ProjectConfig, file_path: str) -> Decision:
    """May the MANAGER edit this file itself?"""
    if current_depth() > 0 or mode(cfg) == "off":
        return Decision(True)
    root = cfg.paths.root.resolve()
    try:
        rel = Path(file_path).resolve().relative_to(root).as_posix()
    except ValueError:
        return Decision(True)                                   # outside the project
    if rel.startswith(cfg.get("worktree.dir", ".agentmesh-worktrees") + "/"):
        return Decision(True)
    allow = cfg.get("enforcement.allow_paths", DEFAULT_ALLOW)
    if matches(rel, allow):
        return Decision(True)
    role = suggest_role(cfg, rel)
    msg = (f"AgentMesh manager mode: do not edit product code directly ({rel}). Delegate it to a role instead:\n"
           f"  agentmesh delegate --role {role or '<role>'} --title \"...\" --description \"...\" --acceptance \"...\" --json\n"
           f"(or plan several stages: agentmesh plan \"<request>\" --emit-batch b.json && agentmesh run-batch b.json).\n"
           f"Then inspect `agentmesh diff <task>`, `agentmesh verify <task>`, `agentmesh integrate <task>`.\n"
           f"Not wanted here? `agentmesh configure set enforcement.mode off` (or warn), or env AGENTMESH_ENFORCE=off.")
    if mode(cfg) == "warn":
        log = cfg.paths.logs_dir / "enforcement.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} direct edit by manager: {rel}\n")
        return Decision(True, msg)
    return Decision(False, msg)


def open_tasks(cfg: ProjectConfig) -> list[str]:
    """Recent tasks that did work but were never verified/integrated, or are still running."""
    window = float(cfg.get("enforcement.stop_window_hours", 12)) * 3600
    out = []
    for t in TaskManager(cfg.paths).list():
        try:
            age = time.time() - datetime.fromisoformat(t.created_at).timestamp()
        except ValueError:
            age = 0
        if age > window or t.reuse_worktree_of or t.capability == "read-only":
            continue
        if t.status in (TaskStatus.RUNNING.value, TaskStatus.SCOPE_VIOLATION.value) or \
                (t.status == TaskStatus.SUCCESS.value and not t.verified):
            out.append(f"{t.task_id} [{t.status}{'' if t.verified else ', not verified'}] {t.role}: {t.title[:50]}")
    return out


def stop_message(cfg: ProjectConfig) -> str | None:
    if current_depth() > 0 or mode(cfg) == "off" or not cfg.get("enforcement.stop_check", True):
        return None
    pending = open_tasks(cfg)
    if not pending:
        return None
    return ("AgentMesh: do not report completion yet. These tasks are not verified/integrated:\n  " + "\n  ".join(pending)
            + "\nFor each: `agentmesh diff <id>`, `agentmesh verify <id>`, then `agentmesh integrate <id>`; "
              "or fix with `agentmesh delegate --continue <id> --message ...`; or drop it with `agentmesh abandon <id>`.")


def audit(cfg: ProjectConfig) -> list[str]:
    """Uncommitted changes in the MAIN tree outside docs/config: work that did not come through a task branch."""
    if gitutil.repo_root(cfg.paths.root) is None:
        return []
    allow = cfg.get("enforcement.allow_paths", DEFAULT_ALLOW)
    ignore = (".agentmesh/", cfg.get("worktree.dir", ".agentmesh-worktrees") + "/")
    return [f for f in gitutil.dirty_files(cfg.paths.root, ignore) if not matches(f, allow)]


def session_summary(cfg: ProjectConfig) -> str:
    from . import discovery
    from .registry import build_registry
    rep = discovery.current(build_registry())
    ready = [a.name for a in rep.agents if a.ready]
    pending = open_tasks(cfg)
    lines = [f"AgentMesh is active for {cfg.name} ({cfg.kind}). You are the MANAGER: delegate, don't implement.",
             f"Roles: {', '.join(cfg.roles())}. Ready workers: {', '.join(ready) or 'none'}. "
             f"Enforcement: {mode(cfg)} (direct product-code edits are {'blocked' if mode(cfg) == 'block' else 'allowed'})."]
    if pending:
        lines.append("Unfinished tasks from earlier:\n  " + "\n  ".join(pending))
    return "\n".join(lines)


# ---------------------------------------------------------------- Claude Code settings

def claude_hook_entries() -> dict[str, list[dict]]:
    cmd = lambda ev: {"type": "command", "command": f"{HOOK_PREFIX} {ev}"}
    return {
        "PreToolUse": [{"matcher": EDIT_TOOLS, "hooks": [cmd("pre-edit")]}],
        "Stop": [{"hooks": [cmd("stop")]}],
        "SessionStart": [{"hooks": [cmd("session-start")]}],
    }


def _is_ours(entry: dict) -> bool:
    return any(str(h.get("command", "")).startswith(HOOK_PREFIX) for h in entry.get("hooks", []))


def install_claude_hooks(root: Path, dry_run: bool = False, remove: bool = False) -> str:
    """Merge our hooks into .claude/settings.json, preserving every other setting and hook. Idempotent."""
    path = root / ".claude" / "settings.json"
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "kept"                    # not valid JSON: never overwrite someone's file
    before = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    for event, entries in claude_hook_entries().items():
        kept = [e for e in hooks.get(event, []) if not _is_ours(e)]
        hooks[event] = kept if remove else [*kept, *entries]
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        data.pop("hooks")
    if json.dumps(data, sort_keys=True) == before:
        return "unchanged"
    existed = path.exists()
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "updated" if existed else "created"


def claude_hooks_installed(root: Path) -> bool:
    path = root / ".claude" / "settings.json"
    try:
        hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, json.JSONDecodeError):
        return False
    return all(any(_is_ours(e) for e in hooks.get(ev, [])) for ev in claude_hook_entries())
