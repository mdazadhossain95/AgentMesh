"""Manager bootstrap: instruction files that tell a manager CLI how to use AgentMesh."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from .fsutil import upsert_block

BEGIN = "<!-- AGENTMESH:BEGIN (managed section; edits inside are overwritten by `agentmesh init`) -->"
END = "<!-- AGENTMESH:END -->"
GI_BEGIN = "# AGENTMESH:BEGIN"
GI_END = "# AGENTMESH:END"

# Instruction file each manager CLI reads. Verified 2026-10-09 by grepping the installed CLIs
# (copilot, cline, kilo, kiro, agy all reference AGENTS.md; copilot also reads CLAUDE.md).
# CLIs not listed fall back to AGENTS.md, the cross-tool convention.
MANAGER_FILES = {
    "claude": "CLAUDE.md", "codex": "AGENTS.md", "opencode": "AGENTS.md", "copilot": "AGENTS.md",
    "cline": "AGENTS.md", "kilo": "AGENTS.md", "kiro": "AGENTS.md", "antigravity": "AGENTS.md",
}
DEFAULT_MANAGER_FILE = "AGENTS.md"


def manager_file(manager: str) -> str:
    return MANAGER_FILES.get(manager, DEFAULT_MANAGER_FILE)


def _template(rel: str) -> str:
    return resources.files("agentmesh").joinpath("templates", *rel.split("/")).read_text(encoding="utf-8")


def manager_section(project_name: str, kind: str, roles: list[str], max_depth: int, max_rounds: int = 2) -> str:
    return _template("manager/section.md").format(
        project_name=project_name, kind=kind, roles=", ".join(f"`{r}`" for r in roles),
        max_depth=max_depth, max_rounds=max_rounds)


def write_manager_files(root: Path, files: list[str], section: str, dry_run: bool = False) -> dict[str, str]:
    return {f: upsert_block(root / f, BEGIN, END, section, dry_run) for f in files}


def update_gitignore(root: Path, dry_run: bool = False) -> str:
    return upsert_block(root / ".gitignore", GI_BEGIN, GI_END, _template("gitignore.txt"), dry_run)


def project_readme() -> str:
    return _template("project/README.md")
