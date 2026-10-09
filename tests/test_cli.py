import json

import pytest

from conftest import commit_all, git, make_script, write, restricted_path
from agentmesh import __version__
from agentmesh.cli import main


@pytest.fixture
def project(flutter_only, monkeypatch, tmp_path):
    """Initialized project whose only workers are mocks (PATH has no real CLIs)."""
    monkeypatch.setenv("PATH", restricted_path(tmp_path/'nobin'))
    monkeypatch.setenv("AGENTMESH_MOCK", "m1:QUOTA_EXCEEDED,m2:SUCCESS,m3:SUCCESS")
    monkeypatch.setenv("AGENTMESH_MOCK_WRITE", "lib/features/profile.dart")
    monkeypatch.chdir(flutter_only)
    assert main(["init", "--auto", "--yes"]) == 0
    assert main(["configure", "set", "routing.preferred_order", '["m1","m2","m3"]']) == 0
    assert main(["configure", "set", "routing.strategy", "preferred-order"]) == 0
    commit_all(flutter_only, "agentmesh init")
    return flutter_only


def run(capsys, *argv):
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_help_and_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "delegate" in out and "init" in out
    assert run(capsys, "version")[1].strip() == f"agentmesh {__version__}"
    code, out, _ = run(capsys)             # no command: prints help
    assert code == 0 and "usage" in out.lower()


def test_commands_outside_project_fail_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, _, err = run(capsys, "status")
    assert code == 1 and "agentmesh init" in err


def test_discover_and_agents_json(capsys, monkeypatch, tmp_path):
    make_script(tmp_path / "bin", "claude", 'case "$1" in --version) echo 1.2.3;; --help) echo "  -p, --print"; echo "  --output-format";; esac')
    monkeypatch.setenv("PATH", restricted_path(tmp_path/'bin'))
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "discover", "--json")
    data = json.loads(out)
    claude = next(a for a in data["agents"] if a["name"] == "claude")
    assert claude["installed"] and claude["state"] == "READY"
    code, out, _ = run(capsys, "agents")
    assert "claude" in out and "never guessed" in out
    code, out, _ = run(capsys, "discover")
    assert "Claude Code" in out and "Installed:          YES" in out


def test_analyze_command(flutter_backend, capsys, monkeypatch):
    monkeypatch.chdir(flutter_backend)
    code, out, _ = run(capsys, "analyze")
    assert code == 0 and "full-stack" in out and "contract-keeper" in out
    assert json.loads(run(capsys, "analyze", "--json")[1])["kind"] == "full-stack"
    assert not (flutter_backend / ".agentmesh").exists()           # analyze never writes


def test_doctor_ready(project, capsys):
    code, out, _ = run(capsys, "doctor")
    assert code == 0 and "Agent Pack" in out and "Workflow" in out and "Overall" in out and "READY" in out
    data = json.loads(run(capsys, "doctor", "--json")[1])
    assert data["overall"].startswith("READY")


def test_doctor_detects_missing_role_file(project, capsys):
    (project / ".agentmesh/agents/reviewer.md").unlink()
    code, out, _ = run(capsys, "doctor")
    assert code == 1 and "NOT READY" in out


def test_doctor_without_project_still_reports_workers(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "doctor")
    assert "not initialized" in out and "Workers" in out


def test_plan_command(project, capsys):
    code, out, _ = run(capsys, "plan", "Fix padding on the screen", "--json", "--risk", "normal")
    data = json.loads(out)
    assert [s["role"] for s in data["stages"] if s["role"] and not s["conditional"]] == ["app-engineer", "test-executor", "reviewer"]
    assert data["stages"][0]["worker_preview"][0] == "m1"      # configured preferred order


def test_delegate_fallback_verify_integrate_end_to_end(project, capsys):
    code, out, err = run(capsys, "delegate", "--role", "app-engineer", "--title", "Profile", "--description", "x", "--json")
    res = json.loads(out)
    assert code == 0 and res["status"] == "SUCCESS" and res["worker"] == "m2"
    assert [a["worker"] for a in res["attempts"]] == ["m1", "m2"] and res["attempts"][0]["normalized_error"] == "QUOTA_EXCEEDED"
    assert res["changed_files"] == ["lib/features/profile.dart"] and "m1 -> QUOTA_EXCEEDED" in err

    code, out, _ = run(capsys, "diff", "task-001", "--stat")
    assert "profile.dart" in out
    code, _, err = run(capsys, "integrate", "task-001")
    assert code == 1 and "not verified" in err                      # cannot merge unverified work
    code, out, _ = run(capsys, "verify", "task-001", "--cmd", "git rev-parse --is-inside-work-tree")
    assert code == 0 and "VERIFIED" in out
    code, out, _ = run(capsys, "integrate", "task-001", "--cleanup")
    assert code == 0 and (project / "lib/features/profile.dart").exists()
    assert not (project / ".agentmesh-worktrees/task-001").exists()
    status = json.loads(run(capsys, "status", "--json")[1])
    assert status["tasks"][0]["status"] == "INTEGRATED"
    assert {w["worker"]: w["runtime"] for w in status["workers"]}["m1"] == "QUOTA_EXCEEDED"


def test_failed_verify_blocks_integration(project, capsys):
    run(capsys, "delegate", "--role", "app-engineer", "--title", "P", "--json")
    code, out, _ = run(capsys, "verify", "task-001", "--cmd", "ls does-not-exist")
    assert code == 1 and "NOT VERIFIED" in out
    assert run(capsys, "integrate", "task-001")[0] == 1


def test_manual_accept_path(project, capsys):
    run(capsys, "delegate", "--role", "app-engineer", "--title", "P", "--json")
    assert run(capsys, "verify", "task-001", "--accept", "reviewed diff by hand")[0] == 0
    assert run(capsys, "integrate", "task-001")[0] == 0


def test_continue_for_focused_correction_with_round_limit(project, capsys):
    run(capsys, "delegate", "--role", "app-engineer", "--title", "P", "--json")
    for _ in range(2):
        code, out, _ = run(capsys, "delegate", "--continue", "task-001", "--message", "fix only X", "--json")
        assert code == 0 and json.loads(out)["task_id"] == "task-001"
    code, _, err = run(capsys, "delegate", "--continue", "task-001", "--message", "again")
    assert code == 1 and "correction rounds" in err
    code, _, err = run(capsys, "delegate", "--continue", "task-001")
    assert code == 1


def test_delegate_validations(project, capsys):
    assert "not part of this project" in run(capsys, "delegate", "--role", "backend-engineer")[2]
    assert "--role is required" in run(capsys, "delegate")[2]


def test_scope_violation_exit_code(project, capsys, monkeypatch):
    monkeypatch.setenv("AGENTMESH_MOCK_WRITE", "backend/evil.py")
    code, out, _ = run(capsys, "delegate", "--role", "app-engineer", "--title", "P")
    assert code == 4 and "VIOLATION" in out


def test_depth_guard_blocks_nested_delegation(project, capsys, monkeypatch):
    monkeypatch.setenv("AGENTMESH_DEPTH", "1")
    code, _, err = run(capsys, "delegate", "--role", "app-engineer", "--title", "P")
    assert code == 1 and "depth" in err


def test_failed_delegate_exit_code_and_no_worker(project, capsys, monkeypatch):
    assert main(["configure", "set", "routing.disabled", '["m1","m2","m3"]']) == 0
    capsys.readouterr()
    code, out, _ = run(capsys, "delegate", "--role", "app-engineer", "--title", "P")
    assert code == 3 and "NO_WORKER_AVAILABLE" in out


def test_configure_refuses_machine_keys(project, capsys):
    code, _, err = run(capsys, "configure", "set", "roles.reviewer.capability", "write")
    assert code == 1 and "overrides" in err


def test_configure_add_agent_registers_generic_cli(capsys, home, tmp_path, monkeypatch):
    exe = make_script(tmp_path / "bin", "mytool", 'case "$1" in --version) echo 3.1;; esac')
    monkeypatch.setenv("PATH", restricted_path(tmp_path/'bin'))
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "configure", "add-agent", "mytool", "--executable", "mytool", "--headless-arg=--go", "--headless-arg={prompt}")
    assert code == 0
    data = json.loads(run(capsys, "discover", "--json")[1])
    mt = next(a for a in data["agents"] if a["name"] == "mytool")
    assert mt["installed"] and mt["state"] == "READY" and mt["version"] == "3.1"


def test_clean(project, capsys):
    run(capsys, "delegate", "--role", "app-engineer", "--title", "P", "--json")
    assert (project / ".agentmesh-worktrees/task-001").exists()
    code, out, _ = run(capsys, "clean", "--worktrees")
    assert "would remove" in out and (project / ".agentmesh-worktrees/task-001").exists()      # dry by default
    run(capsys, "clean", "--worktrees", "--yes")
    assert not (project / ".agentmesh-worktrees/task-001").exists()
    assert (project / ".agentmesh/tasks/task-001.json").exists()                                # records kept


def test_launch_refuses_missing_manager(project, capsys):
    code, _, err = run(capsys, "launch", "claude")
    assert code == 1 and "not installed" in err


def test_smoke_requires_explicit_consent(project, capsys):
    code, out, _ = run(capsys, "smoke", "m2")
    assert code == 1 and "may use paid quota" in out


def test_launch_validates_then_execs_manager(project, capsys, monkeypatch, tmp_path):
    make_script(tmp_path / "bin", "claude", 'case "$1" in --version) echo 1.0;; --help) echo "  -p, --print"; echo "  --output-format";; esac')
    monkeypatch.setenv("PATH", restricted_path(tmp_path/'bin'))
    seen = {}
    monkeypatch.setattr("os.execvpe", lambda path, argv, env: seen.update(path=path, argv=argv, env=env))
    code, out, _ = run(capsys, "launch", "claude", "--", "--resume")
    assert code == 0 and "Launching Claude Code" in out and "Fallback" in out
    assert seen["argv"][1:] == ["--resume"] and seen["env"]["AGENTMESH_DEPTH"] == "0" and seen["env"]["AGENTMESH_MANAGER"] == "claude"


def _quick_res(worker, model, score, n):
    from agentmesh import benchmark as bm
    r = bm.CandidateResult(worker, model, planned=n)
    r.scores = [bm.TaskScore(x.id, score, 1.0) for x in bm.TASKS[:n]]
    return r


def test_cli_benchmark_quick_runs_two_tasks_default_model(project, monkeypatch, capsys):
    from agentmesh import benchmark as bm
    seen = {}
    monkeypatch.setattr(bm, "candidates", lambda reg, infos, lists, only: seen.update(lists=dict(lists)) or [("w1", None)])
    monkeypatch.setattr(bm, "run_benchmark", lambda *a, **k: seen.update(tasks=[t.id for t in k["tasks"]]) or [_quick_res("w1", None, 1.0, 2)])
    code, out, _ = run(capsys, "benchmark", "--quick", "--yes")
    assert code == 0 and seen["tasks"] == list(bm.QUICK_TASKS) and seen["lists"] == {}
    assert "quick rows" in out
    assert bm.load_saved()[0]["quick"] is True


def test_cli_benchmark_quick_rejects_combinations(project, capsys):
    for extra in (["--tasks", "lru"], ["--wide"], ["--apply"]):
        code, _, err = run(capsys, "benchmark", "--quick", "--yes", *extra)
        assert code != 0 and "--quick" in err
