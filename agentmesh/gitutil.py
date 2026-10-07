"""Thin git wrapper. Every call is explicit; nothing here pushes or rewrites history."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import ErrorCode, GitError

IDENTITY = ["-c", "user.name=AgentMesh", "-c", "user.email=agentmesh@localhost"]


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace")
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r


def repo_root(start: Path) -> Path | None:
    r = git(start, "rev-parse", "--show-toplevel", check=False)
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def has_commits(root: Path) -> bool:
    return git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def is_dirty(root: Path, ignore_prefixes: tuple[str, ...] = ()) -> bool:
    return bool(dirty_files(root, ignore_prefixes))


def dirty_files(root: Path, ignore_prefixes: tuple[str, ...] = ()) -> list[str]:
    out = git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
    files = [ln[3:].split(" -> ")[-1].strip('"') for ln in out if ln.strip()]
    return [f for f in files if not f.startswith(ignore_prefixes)]


def changed_since(root: Path, base: str) -> list[str]:
    """Committed + uncommitted + untracked changes relative to `base`, from git itself."""
    committed = git(root, "diff", "--name-only", f"{base}..HEAD", check=False).stdout.splitlines()
    return sorted(set(committed) | set(dirty_files(root)))


def raise_conflict(msg: str) -> None:
    raise GitError(msg, ErrorCode.GIT_CONFLICT)
