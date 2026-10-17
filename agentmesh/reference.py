"""Optional reference material (local folder or git URL). Indexed and linked, never blindly copied."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import ProjectPaths
from .errors import ProjectError

_URL = re.compile(r"^(https?://|git@|ssh://)")

# Default public skill catalogs offered at `init` time (opt-in), filtered to the detected stack.
DEFAULT_SKILL_CATALOGS = [
    "https://github.com/anthropics/skills",
    "https://github.com/tech-leads-club/agent-skills",
    "https://github.com/alirezarezvani/claude-skills",
]

_CORE_SKILL_KEYWORDS = ("core", "spec", "security", "review", "quality", "architecture", "memory",
                        "git", "test", "qa", "document", "pdf", "docx", "pptx", "xlsx", "skill-creator")


def profile_keywords(components: list) -> list[str]:
    """Stack/type/framework keywords used to filter a skill catalog down to what a project needs."""
    kw = {k.lower() for k in _CORE_SKILL_KEYWORDS}
    for c in components:
        kw.add(c.stack.lower())
        kw.add(c.type.lower())
        kw.update(fw.lower() for fw in c.frameworks)
    return sorted(kw)


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


def build_index(ref_root: Path, label: str, keywords: list[str] | None = None) -> str:
    """Markdown index of agent/skill/rule documents found in the reference.

    With `keywords`, skills whose path and headings match none of them are left out of the index
    entirely (they are never fetched into context either) -- used to auto-scope a public skill
    catalog down to what the current project's stack actually needs.
    """
    out = [f"## Reference: {label}", f"Location: `{ref_root}`", ""]
    skip = {".git", "node_modules", ".dart_tool", "build"}
    docs = [p for p in sorted(ref_root.rglob("*.md")) if not (set(p.relative_to(ref_root).parts) & skip)]
    skills = [p for p in docs if p.name == "SKILL.md"]
    agents = [p for p in docs if p.parent.name in ("agents", ".claude", "roles") or "agent" in p.stem.lower()]
    rules = [p for p in docs if re.search(r"(rule|convention|guideline|contributing|architecture)", p.stem, re.I)]
    dropped = 0
    if keywords:
        kw = [k.lower() for k in keywords if k]

        def _match(p: Path) -> bool:
            text = (p.relative_to(ref_root).as_posix() + " " + " ".join(_headings(p, 4))).lower()
            return any(k in text for k in kw)

        kept = [p for p in skills if _match(p)]
        dropped = len(skills) - len(kept)
        skills = kept
    for title, group in (("Role / agent definitions", agents), ("Skills", skills), ("Rules & conventions", rules)):
        if group:
            out.append(f"### {title}")
            for p in group[:40]:
                rel = p.relative_to(ref_root).as_posix()
                h = _headings(p, 2)
                out.append(f"- `{rel}`" + (f" — {'; '.join(h)}" if h else ""))
            out.append("")
    if dropped:
        out.append(f"({dropped} skill(s) in this catalog skipped: no match for this project's detected stack)")
        out.append("")
    if len(out) <= 3:
        out.append("No agent, skill, or rule documents found." if not dropped else
                   "No skills in this catalog matched this project's detected stack.")
    out.append("Use these as methodology. Project-specific business rules in them do NOT apply here unless this "
               "project's own docs say so.")
    return "\n".join(out) + "\n"
