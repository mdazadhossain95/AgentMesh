"""Token-saving advice per project. Read-only: reports what is installed and what is worth it; installs nothing."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BIG_REPO_FILES = 300
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "build", "dist", ".dart_tool", "target", "__pycache__",
             ".agentmesh-worktrees", ".gradle", "Pods"}


@dataclass
class Advice:
    tool: str
    status: str        # READY (in place) | INFO (suggestion or not applicable)
    reason: str


def count_files(root: Path, cap: int = 5000) -> int:
    """Tracked files via git when possible, else a bounded walk. Stops counting at `cap`."""
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return min(len(r.stdout.splitlines()), cap)
    except (OSError, subprocess.SubprocessError):
        pass
    n = 0
    for _, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        n += len(files)
        if n >= cap:
            return cap
    return n


def assess(root: Path, *, mode: str = "auto", verification_commands: int = 0,
           which: Callable[[str], str | None] = shutil.which, home: Path | None = None,
           file_count: int | None = None) -> list[Advice]:
    if mode == "off":
        return []
    home = home or Path.home()
    out: list[Advice] = []

    if verification_commands:
        have = which("rtk")
        out.append(Advice("rtk", "READY" if have else "INFO",
                          f"shell-heavy project ({verification_commands} verification commands); "
                          + ("installed, global hook compresses command output" if have else "not installed: would compress command output")))
    else:
        out.append(Advice("rtk", "INFO", "no verification commands: little shell output to compress"))

    caveman = (home / ".claude" / "plugins" / "cache" / "caveman").is_dir()
    out.append(Advice("caveman", "READY" if caveman else "INFO",
                      "manager sessions run long; " + ("plugin installed (cuts output tokens only)" if caveman
                                                      else "plugin not installed (would cut output tokens only)")))

    n = count_files(root) if file_count is None else file_count
    indexed = (root / ".codegraph").is_dir()
    if n >= BIG_REPO_FILES:
        if indexed:
            out.append(Advice("codegraph", "READY", f"big repo ({n}{'+' if n >= 5000 else ''} files); .codegraph/ index present"))
        elif which("codegraph"):
            out.append(Advice("codegraph", "INFO", f"big repo ({n}{'+' if n >= 5000 else ''} files) and codegraph is installed: "
                              "ask the user before running `codegraph init` (indexing is their decision)"))
        else:
            out.append(Advice("codegraph", "INFO", f"big repo ({n}+ files); codegraph not installed (would cut file searching)"))
    else:
        out.append(Advice("codegraph", "READY" if indexed else "INFO",
                          f"{n} files" + (": indexed" if indexed else f": under {BIG_REPO_FILES}, little gain")))

    out.append(Advice("ponytail", "INFO", "suggest only, for new-feature code; its savings claims are self-reported"))
    return out


def lines(advice: list[Advice]) -> list[str]:
    return [f"{a.tool}: {a.reason}" for a in advice]
