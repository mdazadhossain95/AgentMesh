import pytest

from helpers import make_runner, mock_registry, new_task
from agentmesh.errors import AgentMeshError
from agentmesh.models import TaskStatus

PREF = {"routing": {"strategy": "preferred-order", "preferred_order": ["be", "app", "qa"],
                    "roles": {"backend-engineer": ["be"], "app-engineer": ["app"], "test-executor": ["qa"], "reviewer": ["qa"]}}}


def setup(root, **kw):
    reg = mock_registry({"be": "SUCCESS", "app": "SUCCESS", "qa": "NO_CHANGES"},
                        be={"writes": {"backend/api.py": "api"}}, app={"writes": {"lib/screen.dart": "ui"}}, qa={"writes": {}})
    return (*make_runner(root, reg, PREF), reg)


def test_later_task_sees_earlier_tasks_work(flutter_backend):
    runner, cfg, reg = setup(flutter_backend)
    be = runner.run(new_task(cfg, "backend-engineer"))
    app = runner.run(new_task(cfg, "app-engineer", base_tasks=[be.task_id]))
    assert be.status == app.status == "SUCCESS"
    assert "backend/api.py" in reg.get("app").seen_files[0]                 # visible, built on
    assert app.changed_files == ["lib/screen.dart"]                           # but only its own change is reported
    assert "lib/screen.dart" not in reg.get("be").seen_files[0]
    assert "Your workspace already contains the finished work of: task-001" in reg.get("app").calls[0].stdin


def test_tester_gets_all_branches_combined(flutter_backend):
    runner, cfg, reg = setup(flutter_backend)
    be = runner.run(new_task(cfg, "backend-engineer"))
    app = runner.run(new_task(cfg, "app-engineer"))                            # independent, both from HEAD
    qa = runner.run(new_task(cfg, "test-executor", base_tasks=[be.task_id, app.task_id], isolation="worktree"))
    assert qa.status == "SUCCESS" and qa.changed_files == []
    seen = reg.get("qa").seen_files[0]
    assert "backend/api.py" in seen and "lib/screen.dart" in seen
    assert not (flutter_backend / "backend" / "api.py").exists()               # main still untouched


def test_conflicting_bases_are_refused_cleanly(flutter_backend):
    reg = mock_registry({"be": "SUCCESS", "app": "SUCCESS", "qa": "NO_CHANGES"},
                        be={"writes": {"shared.txt": "A"}}, app={"writes": {"shared.txt": "B"}}, qa={"writes": {}})
    runner, cfg = make_runner(flutter_backend, reg, {**PREF, "overrides": {"roles": {
        "backend-engineer": {"allowed_paths": ["**"]}, "app-engineer": {"allowed_paths": ["**"]}}}})
    a = runner.run(new_task(cfg, "backend-engineer", allowed_paths=["**"]))
    b = runner.run(new_task(cfg, "app-engineer", allowed_paths=["**"]))
    assert a.status == b.status == "SUCCESS"
    qa = runner.run(new_task(cfg, "test-executor", base_tasks=[a.task_id, b.task_id], isolation="worktree"))
    assert qa.status == "FAILED" and qa.normalized_error == "GIT_CONFLICT" and "overlap" in qa.summary
    assert not (flutter_backend / ".agentmesh-worktrees" / qa.task_id).exists()


def test_failed_base_cannot_be_built_on(flutter_backend):
    reg = mock_registry({"be": "QUOTA_EXCEEDED", "app": "SUCCESS", "qa": "SUCCESS"}, app={"writes": {"lib/x.dart": "x"}})
    runner, cfg = make_runner(flutter_backend, reg, {"routing": {"strategy": "preferred-order", "preferred_order": ["be"],
                                                                 "roles": {"backend-engineer": ["be"]}},
                                                     "fallback": {"enabled": False}})
    bad = runner.run(new_task(cfg, "backend-engineer"))
    assert bad.status == "FAILED"
    r = runner.run(new_task(cfg, "app-engineer", base_tasks=[bad.task_id]))
    assert r.status == "FAILED" and "only SUCCESS tasks" in r.summary


def test_integrated_base_is_skipped_since_it_is_in_head(flutter_backend):
    runner, cfg, reg = setup(flutter_backend)
    be = runner.run(new_task(cfg, "backend-engineer"))
    t = runner.tasks.load(be.task_id); t.status = TaskStatus.INTEGRATED.value; runner.tasks.save(t)
    app = runner.run(new_task(cfg, "app-engineer", base_tasks=[be.task_id]))
    assert app.status == "SUCCESS"
