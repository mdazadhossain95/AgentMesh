import pytest

from helpers import make_runner, mock_registry, new_task
from agentmesh.errors import AgentMeshError
from agentmesh.models import AgentInfo
from agentmesh.task_manager import TaskManager

PREF = {"routing": {"strategy": "preferred-order", "preferred_order": ["w1", "w2", "w3", "w4", "w5"]}}
WRITE = {"lib/feature.dart": "// new\n"}


def reg_of(spec, **kw):
    return mock_registry(spec, **{n: {"writes": WRITE} for n in spec}, **kw)


def test_success_changed_files_come_from_git_not_worker_claims(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": WRITE, "report": {"files_changed": ["lib/lie.dart"]}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "SUCCESS" and r.worker == "w1"
    assert r.changed_files == ["lib/feature.dart"] and r.reported_files == ["lib/lie.dart"]
    assert (flutter_only / ".agentmesh-worktrees" / "task-001" / "lib" / "feature.dart").exists()
    assert not (flutter_only / "lib" / "feature.dart").exists()       # main tree untouched
    assert r.branch == "agentmesh/task-001" and r.started_at and r.finished_at


@pytest.mark.parametrize("code", ["QUOTA_EXCEEDED", "RATE_LIMITED", "AUTH_FAILED", "CLI_NOT_FOUND", "TIMEOUT"])
def test_infra_failures_fall_over_to_next_worker_with_same_role(flutter_only, code):
    reg = reg_of({"w1": code, "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "SUCCESS" and r.worker == "w2" and r.role == "app-engineer"
    assert [(a.worker, a.normalized_error) for a in r.attempts] == [("w1", code), ("w2", None)]
    assert runner.state.status("w1") != "OK"


def test_cooldown_makes_next_task_skip_the_exhausted_worker(flutter_only):
    reg = reg_of({"w1": "QUOTA_EXCEEDED", "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    runner.run(new_task(cfg))
    r2 = runner.run(new_task(cfg))
    assert [a.worker for a in r2.attempts] == ["w2"]
    assert len(reg.get("w1").calls) == 1


def test_generic_failure_does_not_fall_back(flutter_only):
    reg = reg_of({"w1": "GENERIC_FAILURE", "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "FAILED" and r.normalized_error == "WORKER_FAILED" and len(r.attempts) == 1
    assert not reg.get("w2").calls


def test_attempt_limit_is_enforced(flutter_only):
    reg = reg_of({f"w{i}": "QUOTA_EXCEEDED" for i in range(1, 6)})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "FAILED" and r.normalized_error == "QUOTA_EXCEEDED" and len(r.attempts) == 3


def test_fallback_can_be_disabled(flutter_only):
    reg = reg_of({"w1": "RATE_LIMITED", "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, {**PREF, "fallback": {"enabled": False}})
    r = runner.run(new_task(cfg))
    assert r.status == "FAILED" and len(r.attempts) == 1


def test_no_worker_available(flutter_only):
    runner, cfg = make_runner(flutter_only, mock_registry({}), PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "FAILED" and r.normalized_error == "NO_WORKER_AVAILABLE" and r.attempts == []


def test_unverified_workers_are_not_used(flutter_only):
    reg = reg_of({"w1": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    runner.infos["w1"].state = "UNVERIFIED"
    assert runner.run(new_task(cfg)).normalized_error == "NO_WORKER_AVAILABLE"


def test_scope_violation_is_flagged_and_not_committed(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": {"backend/secret_thing.py": "x"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "SCOPE_VIOLATION" and "outside allowed paths: backend/secret_thing.py" in r.policy_violations
    import subprocess
    log = subprocess.run(["git", "log", "--oneline", r.branch], cwd=flutter_only, capture_output=True, text=True).stdout
    assert "agentmesh:" not in log


def test_forbidden_path_violation(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": {"lib/.env": "KEY=1"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "SCOPE_VIOLATION"


def test_read_only_role_that_edits_is_a_violation_and_runs_inplace(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": {"lib/oops.dart": "x"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg, "reviewer"))
    assert r.status == "SCOPE_VIOLATION" and r.worktree is None
    assert any("read-only" in v for v in r.policy_violations)


def test_worker_reporting_blocked_is_a_failure(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": WRITE, "report": {"status": "blocked", "summary": "no shell"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    r = runner.run(new_task(cfg))
    assert r.status == "FAILED" and r.normalized_error == "WORKER_FAILED" and "blocked" in r.summary


def test_prompt_carries_role_task_rules_and_handover(flutter_only):
    reg = reg_of({"w1": "QUOTA_EXCEEDED", "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    runner.run(new_task(cfg, title="Profile screen", description="build it", acceptance_criteria=["renders"]))
    first, second = reg.get("w1").calls[0].stdin, reg.get("w2").calls[0].stdin
    for p in (first, second):
        assert "# Role: App Engineer" in p and "Profile screen" in p and "renders" in p and "Do NOT delegate" in p
    assert "Handover" not in first and "stopped with QUOTA_EXCEEDED" in second


def test_continue_reuses_same_worktree_with_focused_message(flutter_only):
    reg = reg_of({"w1": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    t = new_task(cfg)
    r1 = runner.run(t)
    t.retry_count += 1
    r2 = runner.run(t, follow_up="fix only the null check")
    assert r2.worktree == r1.worktree and r2.status == "SUCCESS"
    assert "fix only the null check" in reg.get("w1").calls[-1].stdin and "Follow-up on previous attempt" in reg.get("w1").calls[-1].stdin
    assert "cleanup" not in reg.get("w1").calls[-1].stdin


def test_reuse_worktree_for_review(flutter_only):
    reg = reg_of({"w1": "SUCCESS", "w2": "NO_CHANGES"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    impl = runner.run(new_task(cfg))
    rev = new_task(cfg, "reviewer", reuse_worktree_of=impl.task_id, avoid_workers=["w1"])
    r = runner.run(rev)
    assert r.status == "SUCCESS" and r.worker == "w2" and r.worktree == impl.worktree and r.changed_files == []


def test_delegation_depth_is_capped(flutter_only, monkeypatch):
    reg = reg_of({"w1": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    monkeypatch.setenv("AGENTMESH_DEPTH", "1")
    with pytest.raises(AgentMeshError, match="depth"):
        runner.run(new_task(cfg))
    cfg.data["delegation"]["max_depth"] = 2
    assert runner.run(new_task(cfg)).status == "SUCCESS"


def test_worker_env_marks_depth_and_blocks_recursion(flutter_only):
    reg = reg_of({"w1": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    runner.run(new_task(cfg))
    assert reg.get("w1").calls[0].env["AGENTMESH_DEPTH"] == "1"


def test_results_persist_and_reload(flutter_only):
    reg = reg_of({"w1": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    t = new_task(cfg)
    r = runner.run(t)
    tm = TaskManager(cfg.paths)
    loaded = tm.load_result(t.task_id)
    assert loaded.status == "SUCCESS" and loaded.attempts[0].worker == "w1" and loaded.changed_files == r.changed_files
    assert tm.load(t.task_id).status == "SUCCESS"


def test_logs_are_sanitized(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": WRITE, "report": {"summary": "key sk-ABCDEFGHIJKLMNOP1234 ok"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    adapter = reg.get("w1")
    orig = adapter.execute
    def leaky(spec, run_id="run"):
        raw = orig(spec, run_id)
        raw.stdout += "\nAuthorization: Bearer abcdefghijklmnop1234\nAPI_KEY=supersecretvalue99"
        return raw
    adapter.execute = leaky
    r = runner.run(new_task(cfg))
    blob = r.stdout + (cfg.paths.logs_dir / "task-001-1-w1.log").read_text()
    assert "sk-ABCDEF" not in blob and "supersecretvalue99" not in blob and "abcdefghijklmnop1234" not in blob


def test_worker_model_chain_falls_over_models_before_other_workers(flutter_only):
    reg = reg_of({"w1": ["TIMEOUT", "QUOTA_EXCEEDED", "SUCCESS"], "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, {**PREF, "workers": {"models": {"w1": ["m-a", "m-b", "m-c"]}}})
    r = runner.run(new_task(cfg))
    assert r.status == "SUCCESS" and r.worker == "w1" and not reg.get("w2").calls
    assert [(a.normalized_error, a.note) for a in r.attempts] == [("TIMEOUT", "model=m-a"), ("QUOTA_EXCEEDED", "model=m-b"), (None, "model=m-c")]
    assert [c.argv[-1] for c in reg.get("w1").calls] == ["m-a", "m-b", "m-c"]
    # bad models are skipped next time
    reg.get("w1").behaviors = ["SUCCESS"]
    runner.run(new_task(cfg))
    assert reg.get("w1").calls[-1].argv[-1] == "m-c"


def test_all_models_failing_falls_to_next_worker(flutter_only):
    reg = reg_of({"w1": "RATE_LIMITED", "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, {**PREF, "workers": {"models": {"w1": ["m-a", "m-b"]}}})
    r = runner.run(new_task(cfg))
    assert r.status == "SUCCESS" and r.worker == "w2" and len(reg.get("w1").calls) == 2
