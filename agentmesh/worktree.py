"""Git worktree isolation: one branch + directory per task, never the user's working tree."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import gitutil
from .locking import file_lock
from .errors import AgentMeshError, ErrorCode, ProjectError


class Worktrees:
    def __init__(self, root: Path, dirname: str = ".agentmesh-worktrees"):
        self.root = root
        self.base_dir = root / dirname
        self.dirname = dirname
        self.lock = root / ".agentmesh" / "state" / "git.lock"      # one writer at a time on the shared .git

    def path_for(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def branch_for(self, task_id: str) -> str:
        return f"agentmesh/{task_id}"

    def create(self, task_id: str, base_ref: str = "HEAD", merge: tuple[str, ...] = ()) -> tuple[Path, str, str]:
        """Returns (path, branch, base_commit). `merge` branches are merged in so the task sees all of them;
        base_commit is the head AFTER those merges, so the task's own changes stay separable."""
        with file_lock(self.lock):
            return self._create(task_id, base_ref, merge)

    def _create(self, task_id: str, base_ref: str, merge: tuple[str, ...]) -> tuple[Path, str, str]:
        if not gitutil.has_commits(self.root):
            raise ProjectError("worktree isolation needs at least one commit in the repository")
        path, branch = self.path_for(task_id), self.branch_for(task_id)
        if path.exists():
            raise ProjectError(f"worktree already exists: {path}")
        base = gitutil.git(self.root, "rev-parse", base_ref).stdout.strip()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        gitutil.git(self.root, "worktree", "add", "-b", branch, str(path), base)
        for other in merge:
            r = gitutil.git(path, *gitutil.IDENTITY, "merge", "--no-ff", "-m", f"agentmesh: combine {other} into {task_id}",
                            other, check=False)
            if r.returncode != 0:
                gitutil.git(path, "merge", "--abort", check=False)
                self._remove(task_id, delete_branch=True)
                raise AgentMeshError(f"cannot combine {other} with the other base tasks (merge conflict): "
                                     "those tasks overlap, run them one after another via --base-task", ErrorCode.GIT_CONFLICT)
        return path, branch, gitutil.head(path)

    def exists(self, task_id: str) -> bool:
        return self.path_for(task_id).is_dir()

    def changed_files(self, task_id: str, base_commit: str) -> list[str]:
        return gitutil.changed_since(self.path_for(task_id), base_commit)

    def checkpoint(self, task_id: str, message: str) -> bool:
        """Commit whatever the worker left, on the task branch only. True if a commit was made."""
        wt = self.path_for(task_id)
        if not gitutil.is_dirty(wt):
            return False
        gitutil.git(wt, "add", "-A")
        gitutil.git(wt, *gitutil.IDENTITY, "commit", "-q", "-m", message)
        return True

    def diff(self, task_id: str, base_commit: str, stat: bool = False) -> str:
        wt = self.path_for(task_id)
        gitutil.git(wt, "add", "-N", "-A")          # intent-to-add so untracked files appear in the diff
        args = ["diff", "--stat" if stat else "--patch", base_commit]
        return gitutil.git(wt, *args).stdout

    def remove(self, task_id: str, delete_branch: bool = False) -> None:
        with file_lock(self.lock):
            self._remove(task_id, delete_branch)

    def _remove(self, task_id: str, delete_branch: bool = False) -> None:
        path = self.path_for(task_id)
        if path.exists():
            r = gitutil.git(self.root, "worktree", "remove", "--force", str(path), check=False)
            if r.returncode != 0:
                shutil.rmtree(path, ignore_errors=True)
                gitutil.git(self.root, "worktree", "prune", check=False)
        if delete_branch:
            gitutil.git(self.root, "branch", "-D", self.branch_for(task_id), check=False)

    def integrate(self, task_id: str) -> str:
        with file_lock(self.lock):
            return self._integrate(task_id)

    def _integrate(self, task_id: str) -> str:
        """Merge the task branch into the current branch (no fast-forward, so it is one revertable unit)."""
        if gitutil.is_dirty(self.root, ignore_prefixes=(".agentmesh/", self.dirname + "/")):
            raise ProjectError("main working tree has uncommitted changes; commit or stash before integrating")
        branch = self.branch_for(task_id)
        r = gitutil.git(self.root, *gitutil.IDENTITY, "merge", "--no-ff", "-m",
                        f"agentmesh: integrate {task_id}", branch, check=False)
        if r.returncode != 0:
            gitutil.git(self.root, "merge", "--abort", check=False)
            raise AgentMeshError(f"merge conflict integrating {branch}; merge aborted, nothing changed",
                                 ErrorCode.GIT_CONFLICT)
        return gitutil.head(self.root)
