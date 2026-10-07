"""Worker prompt assembly and report parsing."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import Task, WorkerResult

REPORT_FENCE = re.compile(r"```agentmesh-report\s*(\{.*?\})\s*```", re.S)

REPORT_INSTRUCTIONS = """\
End your final message with exactly one fenced block:

```agentmesh-report
{"status": "done|blocked|failed", "summary": "<=5 lines", "files_changed": ["relative/path"],
 "tests": [{"command": "...", "result": "pass|fail|not-run", "notes": ""}], "notes": "risks, follow-ups"}
```
Report only what you actually did. The manager verifies against git and will not trust claims."""


def parse_report(text: str) -> dict[str, Any]:
    matches = REPORT_FENCE.findall(text or "")
    if not matches:
        return {}
    try:
        data = json.loads(matches[-1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def build_prompt(task: Task, role_markdown: str, project_name: str, *, follow_up: str | None = None,
                 prior: WorkerResult | None = None, fallback_note: str | None = None) -> str:
    parts = [
        f"You are executing role `{task.role}` for project `{project_name}`, launched by AgentMesh.\n"
        f"Your working directory is the isolated workspace. Work only inside it.",
        "## Role definition\n\n" + role_markdown.strip(),
        f"## Task {task.task_id}: {task.title}\n\n{task.description.strip() or '(see requirements)'}",
        "### Requirements\n" + _bullets(task.requirements),
        "### Constraints\n" + _bullets(task.constraints),
        "### Acceptance criteria\n" + _bullets(task.acceptance_criteria),
        "### Verify with\n" + _bullets(task.verification),
        "### Path scope\nAllowed: " + (", ".join(task.allowed_paths) or "(role default)")
        + "\nForbidden: " + (", ".join(task.forbidden_paths) or "(none)"),
        "## Ground rules\n"
        "- You are a worker, not a manager. Do NOT delegate to or launch other AI agents/CLIs.\n"
        "- Stay inside the task scope; do not refactor unrelated code.\n"
        "- Never read, print, or write secrets (.env, keys, tokens). Do not push to any remote.\n"
        + ("- This role is READ-ONLY: do not modify any file.\n" if task.capability == "read-only" else "")
        + "- If you cannot run a command (permission denied), say so in the report instead of guessing results.",
    ]
    if prior is not None and follow_up:
        parts.append(
            "## Follow-up on previous attempt\n"
            f"Previous result: {prior.status}. Summary:\n{prior.summary[-1500:]}\n"
            f"Files already changed: {', '.join(prior.changed_files) or '(none)'}\n\n"
            f"Correction requested by the manager (fix ONLY this, leave everything else alone):\n{follow_up}")
    if fallback_note:
        parts.append("## Handover\n" + fallback_note)
    parts.append("## Report\n" + REPORT_INSTRUCTIONS)
    return "\n\n".join(parts) + "\n"


def read_role(agents_dir: Path, role: str) -> str:
    p = agents_dir / f"{role}.md"
    return p.read_text(encoding="utf-8") if p.is_file() else f"# Role\n\n{role}\n"
