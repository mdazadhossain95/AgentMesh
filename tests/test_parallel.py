import json
import threading
import time

import pytest

from conftest import commit_all, write
from helpers import make_runner, mock_registry
from agentmesh.batch import BatchItem, load_batch, run_batch, template_from_plan, validate
from agentmesh.delegation import TaskSpec
from agentmesh.errors import AgentMeshError
from agentmesh.state import RuntimeState
from agentmesh.task_manager import TaskManager
from agentmesh.workflow import generate_workflow, plan_task

ROUTE = {"routing": {"strategy": "preferred-order", "preferred_order": ["be", "app", "qa"],
                     "roles": {"backend-engineer": ["be"], "app-engineer": ["app"], "test-executor": ["qa"], "reviewer": ["qa", "app"],
                               "spec-analyst": ["qa"], "contract-keeper": ["qa"]}}}


def env(root, delay=0.0, **over):
    reg = mock_registry({"be": "SUCCESS", "app": "SUCCESS", "qa": "NO_CHANGES"},
                        be={"writes": {"backend/api.py": "api"}, "delay": delay},
                        app={"writes": {"lib/screen.dart": "ui"}, "delay": delay}, qa={"writes": {}, **over.get("qa", {})})
    runner, cfg = make_runner(root, reg, ROUTE)
    return runner, cfg, reg


def item(i, role, deps=()):
    return BatchItem(i, TaskSpec(role=role, title=i, description=f"do {i}"), list(deps))


def test_independent_tasks_really_run_at_the_same_time(flutter_backend):
    runner, cfg, _ = env(flutter_backend, delay=0.6)
    items = [item("be", "backend-engineer"), item("ui", "app-engineer")]
    t = time.monotonic()
    out = run_batch(runner, cfg, items, max_parallel=2)
    parallel = time.monotonic() - t
    assert {o.status for o in out.values()} == {"SUCCESS"}
    assert parallel < 1.1                                           # two 0.6s jobs overlapped (sequential would be >= 1.2)


def test_max_parallel_one_is_sequential(flutter_backend):
    runner, cfg, _ = env(flutter_backend, delay=0.4)
    t = time.monotonic()
    run_batch(runner, cfg, [item("be", "backend-engineer"), item("ui", "app-engineer")], max_parallel=1)
    assert time.monotonic() - t >= 0.8


def test_dependent_tasks_wait_and_see_all_inputs(flutter_backend):
    runner, cfg, reg = env(flutter_backend, delay=0.2)
    items = [item("be", "backend-engineer"), item("ui", "app-engineer"),
             item("test", "test-executor", ["be", "ui"]), item("review", "reviewer", ["be", "ui", "test"])]
    out = run_batch(runner, cfg, items, max_parallel=3)
    assert [o.status for o in out.values()] == ["SUCCESS"] * 4
    seen = reg.get("qa").seen_files
    assert "backend/api.py" in seen[0] and "lib/screen.dart" in seen[0]          # tester got both branches combined
    assert "backend/api.py" in seen[1] and "lib/screen.dart" in seen[1]          # and so did the reviewer
    prompt = reg.get("qa").calls[1].stdin
    assert "Inputs from earlier stages" in prompt and out["be"].task_id in prompt   # earlier reports passed along
    # reviewer prefers a CLI that did not write the code; qa did not write it
    assert out["review"].result.worker == "qa"


def test_read_only_dependency_becomes_context_not_a_base(flutter_backend):
    runner, cfg, reg = env(flutter_backend)
    items = [item("spec", "spec-analyst"), item("be", "backend-engineer", ["spec"])]
    out = run_batch(runner, cfg, items, max_parallel=2)
    assert [o.status for o in out.values()] == ["SUCCESS", "SUCCESS"]
    t = TaskManager(cfg.paths).load(out["be"].task_id)
    assert t.context_tasks == [out["spec"].task_id] and t.base_tasks == []
    assert "Inputs from earlier stages" in reg.get("be").calls[0].stdin


def test_failed_dependency_skips_dependents_but_not_independent_work(flutter_backend):
    reg = mock_registry({"be": "GENERIC_FAILURE", "app": "SUCCESS", "qa": "NO_CHANGES"},
                        app={"writes": {"lib/screen.dart": "ui"}}, qa={"writes": {}})
    runner, cfg = make_runner(flutter_backend, reg, ROUTE)
    items = [item("be", "backend-engineer"), item("ui", "app-engineer"), item("test", "test-executor", ["be", "ui"])]
    out = run_batch(runner, cfg, items, max_parallel=3)
    assert (out["be"].status, out["ui"].status, out["test"].status) == ("FAILED", "SUCCESS", "SKIPPED")
    assert "be" in out["test"].note and not reg.get("qa").calls


def test_fail_fast_stops_launching(flutter_backend):
    reg = mock_registry({"be": "GENERIC_FAILURE", "app": "SUCCESS", "qa": "NO_CHANGES"}, app={"writes": {"lib/s.dart": "x"}})
    runner, cfg = make_runner(flutter_backend, reg, ROUTE)
    out = run_batch(runner, cfg, [item("be", "backend-engineer"), item("ui", "app-engineer", ["be"]),
                                  item("x", "reviewer")], max_parallel=1, fail_fast=True)
    assert out["be"].status == "FAILED" and out["ui"].status == "SKIPPED"


def test_fallback_still_works_inside_parallel_tasks(flutter_backend):
    reg = mock_registry({"be": "QUOTA_EXCEEDED", "app": "SUCCESS", "qa": "NO_CHANGES"},
                        app={"writes": {"backend/api.py": "api"}}, qa={"writes": {}})
    runner, cfg = make_runner(flutter_backend, reg, {"routing": {"strategy": "preferred-order", "preferred_order": ["be", "app", "qa"]}})
    out = run_batch(runner, cfg, [item("be", "backend-engineer"), item("be2", "backend-engineer")], max_parallel=2)
    assert {o.status for o in out.values()} == {"SUCCESS"}
    assert {o.result.worker for o in out.values()} == {"app"}                  # both moved off the exhausted worker


# ---- validation
def test_batch_validation(tmp_path):
    with pytest.raises(AgentMeshError, match="unknown"):
        validate([item("a", "x", ["zzz"])])
    with pytest.raises(AgentMeshError, match="cycle"):
        validate([item("a", "x", ["b"]), item("b", "x", ["a"])])
    with pytest.raises(AgentMeshError, match="duplicate"):
        validate([item("a", "x"), item("a", "x")])
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"tasks": [{"id": "a", "role": "r"}, {"id": "b", "role": "r", "depends_on": ["a"]}]}))
    assert [i.depends_on for i in load_batch(f)] == [[], ["a"]]
    f.write_text("nope")
    with pytest.raises(AgentMeshError):
        load_batch(f)


def test_plan_emits_a_parallel_batch(flutter_backend):
    runner, cfg, _ = env(flutter_backend)
    wf = generate_workflow("x", "full-stack", list(cfg.roles()))
    plan = plan_task(wf, "Implement the profile API and Flutter profile screen")
    batch = template_from_plan(plan.to_dict(), "profile")
    deps = {t["id"]: t["depends_on"] for t in batch["tasks"]}
    assert deps["spec"] == [] and deps["contract"] == ["spec"]
    assert deps["implement-backend"] == deps["implement-app"] == ["contract"]          # siblings: run in parallel
    assert deps["test"] == ["implement-backend", "implement-app"]
    assert deps["review"] == ["implement-backend", "implement-app", "test"]
    validate([item(t["id"], t["role"], t["depends_on"]) for t in batch["tasks"]])


# ---- shared-state safety under real concurrency
def test_task_ids_are_unique_under_concurrency(flutter_only):
    runner, cfg = make_runner(flutter_only, mock_registry({"w": "SUCCESS"}))
    tm = TaskManager(cfg.paths)
    ids, lock = [], threading.Lock()
    def grab():
        for _ in range(10):
            i = tm.next_id()
            with lock: ids.append(i)
    ts = [threading.Thread(target=grab) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(ids) == 80 and len(set(ids)) == 80
    assert tm.list() == []                                              # reserved-but-unsaved ids are skipped, not crashes


def test_state_counters_survive_concurrent_writers(tmp_path):
    path = tmp_path / "s" / "workers.json"
    def work():
        st = RuntimeState(path)               # separate instances, like separate processes
        for _ in range(10):
            st.record_use("w")
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert RuntimeState(path).uses("w") == 80


def test_cli_run_batch_end_to_end(flutter_backend, monkeypatch, capsys, tmp_path):
    from agentmesh.cli import main
    monkeypatch.setenv("PATH", f"{tmp_path/'nobin'}:/usr/bin:/bin")
    monkeypatch.setenv("AGENTMESH_MOCK", "m1:SUCCESS,m2:SUCCESS")
    monkeypatch.setenv("AGENTMESH_MOCK_WRITE", "lib/f.dart")
    monkeypatch.chdir(flutter_backend)
    assert main(["init", "--auto", "--yes"]) == 0
    commit_all(flutter_backend, "init")
    capsys.readouterr()
    assert main(["plan", "Fix padding on the profile screen", "--emit-batch", "b.json"]) == 0
    capsys.readouterr()
    code = main(["run-batch", "b.json", "--json", "--max-parallel", "2"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["ok"] and set(data["tasks"]) == {"implement-app", "test", "review"}
    assert all(t["status"] == "SUCCESS" for t in data["tasks"].values())
