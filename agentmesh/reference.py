"""Optional reference material (local folder or git URL). Indexed and linked, never blindly copied."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import ProjectPaths
from .errors import ProjectError

_URL = re.compile(r"^(https?://|git@|ssh://)")


def resolve_reference(ref: str, paths: ProjectPaths) -> Path:
    if _URL.match(ref):
        target = paths.runtime_dir / "references" / re.sub(r"[^A-Za-z0-9._-]", "_", ref.rstrip("/").split("/")[-1].removesuffix(".git"))
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", "--depth", "1", ref, str(target)], capture_output=True, text=True)
            if r.returncode != 0:
                raise ProjectError(f"could not clone reference {ref}: {r.stderr.strip()[-300:]}")
        return target
    p = Path(ref).expanduser().resolve()
    if not p.exists():
        raise ProjectError(f"reference path not found: {p}")
    return p


def _headings(md: Path, limit: int = 6) -> list[str]:
    try:
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines()[:200]
    except OSError:
        return []
    return [ln.lstrip("# ").strip() for ln in lines if re.match(r"#{1,2} ", ln)][:limit]


def build_index(ref_root: Path, label: str) -> str:
    """Markdown index of agent/skill/rule documents found in the reference."""
    out = [f"## Reference: {label}", f"Location: `{ref_root}`", ""]
    skip = {".git", "node_modules", ".dart_tool", "build"}
    docs = [p for p in sorted(ref_root.rglob("*.md")) if not (set(p.relative_to(ref_root).parts) & skip)]
    skills = [p for p in docs if p.name == "SKILL.md"]
    agents = [p for p in docs if p.parent.name in ("agents", ".claude", "roles") or "agent" in p.stem.lower()]
    rules = [p for p in docs if re.search(r"(rule|convention|guideline|contributing|architecture)", p.stem, re.I)]
    for title, group in (("Role / agent definitions", agents), ("Skills", skills), ("Rules & conventions", rules)):
        if group:
            out.append(f"### {title}")
            for p in group[:40]:
                rel = p.relative_to(ref_root).as_posix()
                h = _headings(p, 2)
                out.append(f"- `{rel}`" + (f" — {'; '.join(h)}" if h else ""))
            out.append("")
    if len(out) <= 3:
        out.append("No agent, skill, or rule documents found.")
    out.append("Use these as methodology. Project-specific business rules in them do NOT apply here unless this "
               "project's own docs say so.")
    return "\n".join(out) + "\n"
