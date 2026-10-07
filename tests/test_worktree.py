import pytest

from conftest import commit_all, git, write
from agentmesh.errors import AgentMeshError, ErrorCode, ProjectError
from agentmesh.worktree import Worktrees


def test_create_isolated_worktree_and_branch(repo):
    wt = Worktrees(repo)
    path, branch, base = wt.create("task-001")
    assert path == repo / ".agentmesh-worktrees" / "task-001" and branch == "agentmesh/task-001"
    assert (path / "README.md").exists() and base == git(repo, "rev-parse", "HEAD").strip()
    write(path, "new.txt", "x")
    assert not (repo / "new.txt").exists()
    assert wt.changed_files("task-001", base) == ["new.txt"]


def test_changed_files_include_committed_modified_untracked(repo):
    wt = Worktrees(repo)
    path, _, base = wt.create("t1")
    write(path, "README.md", "changed\n")
    git(path, "add", "-A"); git(path, "commit", "-qm", "w")
    write(path, "untracked.txt")
    assert wt.changed_files("t1", base) == ["README.md", "untracked.txt"]


def test_checkpoint_commits_only_on_task_branch(repo):
    wt = Worktrees(repo)
    path, branch, base = wt.create("t1")
    assert wt.checkpoint("t1", "nothing") is False
    write(path, "a.txt")
    assert wt.checkpoint("t1", "agentmesh: t1") is True
    assert "agentmesh: t1" in git(repo, "log", "--oneline", branch)
    assert "agentmesh: t1" not in git(repo, "log", "--oneline", "main")


def test_requires_a_commit(tmp_path):
    root = tmp_path / "empty"; root.mkdir()
    git(root, "init", "-q")
    with pytest.raises(ProjectError, match="commit"):
        Worktrees(root).create("t1")


def test_two_tasks_do_not_touch_each_other(repo):
    wt = Worktrees(repo)
    p1, *_ = wt.create("t1"); p2, *_ = wt.create("t2")
    write(p1, "one.txt"); write(p2, "two.txt")
    assert not (p1 / "two.txt").exists() and not (p2 / "one.txt").exists()


def test_diff_shows_untracked_files(repo):
    wt = Worktrees(repo)
    path, _, base = wt.create("t1")
    write(path, "fresh.txt", "hello\n")
    assert "fresh.txt" in wt.diff("t1", base, stat=True) and "+hello" in wt.diff("t1", base)


def test_integrate_merges_with_merge_commit(repo):
    wt = Worktrees(repo)
    path, _, _ = wt.create("t1")
    write(path, "feat.txt", "f"); wt.checkpoint("t1", "work")
    sha = wt.integrate("t1")
    assert (repo / "feat.txt").exists() and len(git(repo, "rev-list", "--parents", "-n1", sha).split()) == 3


def test_integrate_refuses_dirty_main_tree(repo):
    wt = Worktrees(repo)
    path, _, _ = wt.create("t1")
    write(path, "feat.txt"); wt.checkpoint("t1", "w")
    write(repo, "README.md", "dirty\n")
    with pytest.raises(ProjectError, match="uncommitted"):
        wt.integrate("t1")


def test_integrate_conflict_aborts_cleanly(repo):
    wt = Worktrees(repo)
    path, _, _ = wt.create("t1")
    write(path, "README.md", "from worker\n"); wt.checkpoint("t1", "w")
    write(repo, "README.md", "from main\n"); commit_all(repo, "main edit")
    with pytest.raises(AgentMeshError) as e:
        wt.integrate("t1")
    assert e.value.code == ErrorCode.GIT_CONFLICT
    assert (repo / "README.md").read_text() == "from main\n" and git(repo, "status", "--porcelain").strip() == ""


def test_remove(repo):
    wt = Worktrees(repo)
    path, branch, _ = wt.create("t1")
    wt.remove("t1", delete_branch=True)
    assert not path.exists() and branch not in git(repo, "branch")
