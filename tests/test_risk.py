import json

import pytest

from conftest import commit_all, write
from helpers import init_with, make_runner, mock_registry, new_task
from agentmesh import risk
from agentmesh.cli import main
from agentmesh.config import ProjectConfig, load_yaml
from agentmesh.delegation import TaskSpec, build_task
from agentmesh.task_manager import TaskManager
from agentmesh.workflow import plan_task

PREF = {"routing": {"strategy": "preferred-order", "preferred_order": ["w1", "w2", "w3"]}}
WRITE = {"lib/feature.dart": "// new\n"}


# ---- classifier ----
@pytest.mark.parametrize("text,files,tier", [
    ("Fix typo in the settings label", [], "low"),
    ("Update README wording", ["README.md"], "low"),
    ("Add profile screen", ["lib/profile.dart"], "normal"),
    ("Add login with JWT", [], "high"),
    ("Tweak padding", ["backend/api/users.py"], "high"),
    ("Refactor helper", ["db/migrations/001.sql"], "high"),
    ("Fix checkout refund bug", [], "high"),
    ("Fix typo", ["lib/a.dart"], "normal"),
])
def test_assess_tiers(text, files, tier):
    assert risk.assess(text, files).tier == tier


def test_assess_override_and_config_paths():
    assert risk.assess("Add login", [], override="low").tier == "low"
    with pytest.raises(ValueError):
        risk.assess("x", [], override="extreme")
    assert risk.assess("Tweak", ["lib/special/x.dart"], high_paths=["lib/special/**"]).tier == "high"
    assert risk.assess("Tweak", ["notes/a.dat"], low_paths=["notes/**"]).tier == "low"


# ---- plan shape ----
WF = lambda root: load_yaml(root / ".agentmesh" / "workflow.yaml")


def roles(plan):
    return [s.role for s in plan.stages if s.role and not s.conditional]


def test_plan_low_normal_high(flutter_only):
    init_with(flutter_only, mock_registry({"w1": "SUCCESS", "w2": "SUCCESS"}))
    wf = WF(flutter_only)
    assert roles(plan_task(wf, "Fix padding on the screen", risk="low")) == ["app-engineer"]
    assert roles(plan_task(wf, "Fix padding on the screen", risk="normal")) == ["app-engineer", "test-executor", "reviewer"]
    high = plan_task(wf, "Fix padding on the screen", risk="high")
    assert "spec-analyst" in roles(high) and roles(high)[-1] == "reviewer"
    assert plan_task(wf, "Fix padding", risk="low").stages[-1].actor == "manager"      # manager check always stays


def test_review_mode_never_and_always(flutter_only):
    init_with(flutter_only, mock_registry({"w1": "SUCCESS"}))
    wf = WF(flutter_only)
    assert "reviewer" not in roles(plan_task(wf, "Add screen", risk="high", review_mode="never"))
    assert "reviewer" in roles(plan_task(wf, "Fix padding", risk="low", review_mode="always"))


def test_cli_plan_reports_risk(flutter_only, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PATH", f"{tmp_path/'nobin'}:/usr/bin:/bin")
    monkeypatch.setenv("AGENTMESH_MOCK", "m1:SUCCESS,m2:SUCCESS")
    monkeypatch.chdir(flutter_only)
    main(["init", "--auto", "--yes"]); capsys.readouterr()
    main(["plan", "Fix typo in label", "--json"])
    assert json.loads(capsys.readouterr().out)["risk"] == "low"
    main(["plan", "Fix typo in label", "--json", "--risk", "high"])
    d = json.loads(capsys.readouterr().out)
    assert d["risk"] == "high" and d["risk_reasons"] == ["set by --risk"]


def test_task_risk_classified_overridden_and_inherited(flutter_only):
    reg = mock_registry({"w1": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    tm = TaskManager(cfg.paths)
    assert build_task(cfg, tm, TaskSpec(role="app-engineer", title="Fix typo")).risk == "low"
    t = build_task(cfg, tm, TaskSpec(role="app-engineer", title="Add JWT login"))
    assert t.risk == "high"
    assert build_task(cfg, tm, TaskSpec(role="app-engineer", title="x", risk="normal")).risk == "normal"
    rev = build_task(cfg, tm, TaskSpec(role="reviewer", title="look", reuse_worktree=t.task_id, inplace=True))
    assert rev.risk == "high"          # review of risky work is risky


# ---- second-agent review ----
def run_impl_then_review(root, spec, risk_tier, settings=None):
    reg = mock_registry(spec, **{n: {"writes": WRITE} for n in spec})
    runner, cfg = make_runner(root, reg, {**PREF, **(settings or {})})
    impl = runner.run(new_task(cfg, risk=risk_tier))
    for n in spec:
        reg.get(n).writes = {}
    rev = new_task(cfg, "reviewer", reuse_worktree_of=impl.task_id, risk=risk_tier)
    return runner, cfg, impl, runner.run(rev)


def test_reviewer_goes_to_a_different_worker(flutter_only):
    _, _, impl, rev = run_impl_then_review(flutter_only, {"w1": "SUCCESS", "w2": "SUCCESS"}, "normal")
    assert impl.worker == "w1" and rev.status == "SUCCESS" and rev.worker == "w2"


def test_single_worker_reviews_own_code_with_note_when_not_high(flutter_only):
    _, _, impl, rev = run_impl_then_review(flutter_only, {"w1": "SUCCESS"}, "normal")
    assert rev.worker == "w1" and "also wrote the code" in rev.summary


def test_high_risk_review_refuses_the_author(flutter_only):
    _, _, impl, rev = run_impl_then_review(flutter_only, {"w1": "SUCCESS"}, "high")
    assert rev.status == "FAILED" and rev.normalized_error == "NO_WORKER_AVAILABLE" and "wrote the code" in rev.summary


def test_require_different_worker_can_be_turned_off(flutter_only):
    _, _, _, rev = run_impl_then_review(flutter_only, {"w1": "SUCCESS"}, "high",
                                        {"review": {"require_different_worker": False}})
    assert rev.status == "SUCCESS" and rev.worker == "w1"


# ---- verify / integrate gating (CLI) ----
@pytest.fixture
def proj(flutter_only, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PATH", f"{tmp_path/'nobin'}:/usr/bin:/bin")
    monkeypatch.setenv("AGENTMESH_MOCK", "m1:SUCCESS,m2:SUCCESS")
    monkeypatch.setenv("AGENTMESH_MOCK_WRITE", "lib/f.dart")
    monkeypatch.chdir(flutter_only)
    assert main(["init", "--auto", "--yes"]) == 0
    commit_all(flutter_only, "init")
    capsys.readouterr()
    return flutter_only


def test_low_risk_docs_change_verifies_by_diff_check(proj, monkeypatch, capsys):
    monkeypatch.setenv("AGENTMESH_MOCK_WRITE", "docs/guide.md")
    assert main(["delegate", "--role", "app-engineer", "--title", "Fix typo in guide", "--description", "typo", "--risk", "low", "--allow", "docs/**"]) == 0
    capsys.readouterr()
    assert main(["verify", "task-001"]) == 0
    assert "diff check only" in capsys.readouterr().out


def test_low_risk_code_change_still_runs_commands(proj, capsys):
    main(["delegate", "--role", "app-engineer", "--title", "Fix typo", "--description", "t", "--risk", "low"])
    capsys.readouterr()
    main(["verify", "task-001"])
    assert "diff check only" not in capsys.readouterr().out


def test_high_risk_integrate_needs_a_successful_review(proj, capsys):
    assert main(["delegate", "--role", "app-engineer", "--title", "Add JWT login", "--description", "x"]) == 0
    assert main(["verify", "task-001", "--accept", "manual"]) == 0
    capsys.readouterr()
    assert main(["integrate", "task-001"]) != 0
    assert "high risk" in capsys.readouterr().err
    main(["delegate", "--role", "reviewer", "--title", "review", "--description", "r", "--reuse-worktree", "task-001"])
    capsys.readouterr()
    assert main(["integrate", "task-001"]) == 0


def test_review_never_skips_the_integrate_gate(proj, capsys):
    main(["delegate", "--role", "app-engineer", "--title", "Add JWT login", "--description", "x"])
    main(["verify", "task-001", "--accept", "manual"])
    main(["configure", "set", "review.mode", "never"])
    capsys.readouterr()
    assert main(["integrate", "task-001"]) == 0
