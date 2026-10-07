import io
import json

import pytest

from conftest import commit_all, write
from helpers import init_with, make_runner, mock_registry, new_task
from agentmesh import enforcement
from agentmesh.cli import main
from agentmesh.config import ProjectConfig, dump_yaml, load_yaml


@pytest.fixture
def proj(flutter_backend):
    init_with(flutter_backend, mock_registry({"w1": "SUCCESS"}))
    commit_all(flutter_backend, "init")
    return ProjectConfig.load(flutter_backend)


def set_cfg(cfg, **kv):
    p = cfg.paths.project_yaml
    d = load_yaml(p)
    d.setdefault("enforcement", {}).update(kv)
    p.write_text(dump_yaml(d))
    return ProjectConfig.load(cfg.paths.root)


def test_manager_cannot_edit_product_code_and_is_told_which_role(proj):
    d = enforcement.decide_edit(proj, str(proj.paths.root / "lib" / "features" / "x.dart"))
    assert not d.allow and "agentmesh delegate --role app-engineer" in d.message
    d = enforcement.decide_edit(proj, str(proj.paths.root / "backend" / "svc.py"))
    assert not d.allow and "--role backend-engineer" in d.message


@pytest.mark.parametrize("rel", ["README.md", "docs/guide.txt", ".agentmesh/project.yaml", "CLAUDE.md", ".gitignore", "notes/x.md"])
def test_docs_and_config_are_allowed(proj, rel):
    assert enforcement.decide_edit(proj, str(proj.paths.root / rel)).allow


def test_files_outside_the_project_are_allowed(proj, tmp_path):
    assert enforcement.decide_edit(proj, str(tmp_path / "elsewhere.py")).allow


def test_workers_are_never_blocked(proj, monkeypatch):
    monkeypatch.setenv("AGENTMESH_DEPTH", "1")
    assert enforcement.decide_edit(proj, str(proj.paths.root / "lib" / "x.dart")).allow


def test_modes(proj, monkeypatch):
    target = str(proj.paths.root / "lib" / "x.dart")
    warn = set_cfg(proj, mode="warn")
    d = enforcement.decide_edit(warn, target)
    assert d.allow and d.message and "x.dart" in (proj.paths.logs_dir / "enforcement.log").read_text()
    assert enforcement.decide_edit(set_cfg(proj, mode="off"), target).allow
    monkeypatch.setenv("AGENTMESH_ENFORCE", "off")
    assert enforcement.decide_edit(set_cfg(proj, mode="block"), target).allow


def hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)))
    code = main(["hook", event])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_pre_edit_hook_protocol(proj, monkeypatch, capsys):
    root = proj.paths.root
    code, _, err = hook(monkeypatch, capsys, "pre-edit", {"cwd": str(root), "tool_name": "Edit",
                                                          "tool_input": {"file_path": str(root / "lib" / "main.dart")}})
    assert code == 2 and "do not edit product code" in err
    code, _, _ = hook(monkeypatch, capsys, "pre-edit", {"cwd": str(root), "tool_name": "Write",
                                                        "tool_input": {"file_path": str(root / "README.md")}})
    assert code == 0
    code, _, err = hook(monkeypatch, capsys, "pre-edit", {"cwd": str(root), "tool_name": "Edit",
                                                          "tool_input": {"file_path": "lib/main.dart"}})     # relative path
    assert code == 2
    code, *_ = hook(monkeypatch, capsys, "pre-edit", {"cwd": str(root), "tool_name": "NotebookEdit",
                                                      "tool_input": {"notebook_path": str(root / "lib" / "n.ipynb")}})
    assert code == 2


def test_hooks_fail_open(proj, monkeypatch, capsys, tmp_path):
    assert hook(monkeypatch, capsys, "pre-edit", "not json at all")[0] == 0
    assert hook(monkeypatch, capsys, "pre-edit", {"cwd": str(tmp_path), "tool_input": {"file_path": str(tmp_path / "a.py")}})[0] == 0
    assert hook(monkeypatch, capsys, "stop", {"cwd": str(tmp_path)})[0] == 0                      # not an AgentMesh project
    assert hook(monkeypatch, capsys, "pre-edit", {"cwd": str(proj.paths.root), "tool_input": {}})[0] == 0
    monkeypatch.setattr(enforcement, "decide_edit", lambda *a: 1 / 0)
    code, _, err = hook(monkeypatch, capsys, "pre-edit", {"cwd": str(proj.paths.root),
                                                          "tool_input": {"file_path": str(proj.paths.root / "lib" / "a.dart")}})
    assert code == 0 and "ignored internal error" in err


def test_stop_hook_blocks_until_work_is_verified(flutter_backend, monkeypatch, capsys):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": {"lib/a.dart": "a"}})
    runner, cfg = make_runner(flutter_backend, reg, {"routing": {"strategy": "preferred-order", "preferred_order": ["w1"]}})
    commit_all(flutter_backend, "init")
    monkeypatch.chdir(flutter_backend)
    payload = {"cwd": str(flutter_backend)}
    assert hook(monkeypatch, capsys, "stop", payload)[0] == 0                      # nothing open
    r = runner.run(new_task(cfg))
    assert r.status == "SUCCESS"
    code, _, err = hook(monkeypatch, capsys, "stop", payload)
    assert code == 2 and r.task_id in err and "agentmesh verify" in err
    assert hook(monkeypatch, capsys, "stop", {**payload, "stop_hook_active": True})[0] == 0   # never loops
    assert main(["verify", r.task_id, "--accept", "checked"]) == 0
    capsys.readouterr()
    assert hook(monkeypatch, capsys, "stop", payload)[0] == 0                      # verified -> may finish
    r2 = runner.run(new_task(cfg))
    assert hook(monkeypatch, capsys, "stop", payload)[0] == 2
    assert main(["abandon", r2.task_id]) == 0
    capsys.readouterr()
    assert hook(monkeypatch, capsys, "stop", payload)[0] == 0                      # abandoned -> may finish


def test_stop_hook_ignores_old_tasks_and_workers(flutter_backend, monkeypatch, capsys):
    reg = mock_registry({"w1": "SUCCESS"}, w1={"writes": {"lib/a.dart": "a"}})
    runner, cfg = make_runner(flutter_backend, reg)
    r = runner.run(new_task(cfg))
    monkeypatch.setenv("AGENTMESH_DEPTH", "1")
    assert hook(monkeypatch, capsys, "stop", {"cwd": str(flutter_backend)})[0] == 0
    monkeypatch.delenv("AGENTMESH_DEPTH")
    cfg = set_cfg(cfg, stop_window_hours=0)                                          # everything is "old"
    assert hook(monkeypatch, capsys, "stop", {"cwd": str(flutter_backend)})[0] == 0
    assert r.task_id


def test_session_start_prints_status(proj, monkeypatch, capsys):
    code, out, _ = hook(monkeypatch, capsys, "session-start", {"cwd": str(proj.paths.root)})
    assert code == 0 and "You are the MANAGER" in out and "Enforcement: block" in out


def test_audit_finds_direct_changes(proj, capsys):
    root = proj.paths.root
    assert main(["audit", "--path", str(root)]) == 0
    write(root, "README.md", "doc edit is fine\n")
    write(root, "lib/sneaky.dart", "x")
    assert main(["audit", "--path", str(root)]) == 1
    out = capsys.readouterr().out
    assert "lib/sneaky.dart" in out and "README.md" not in out


# ---- settings installation
def test_install_creates_and_is_idempotent(tmp_path):
    assert enforcement.install_claude_hooks(tmp_path) == "created"
    assert enforcement.install_claude_hooks(tmp_path) == "unchanged"
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert set(data["hooks"]) == {"PreToolUse", "Stop", "SessionStart"}
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write|MultiEdit|NotebookEdit"
    assert enforcement.claude_hooks_installed(tmp_path)


def test_install_preserves_existing_settings_and_hooks(tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    mine = {"model": "x", "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}]}}
    p.write_text(json.dumps(mine))
    assert enforcement.install_claude_hooks(tmp_path) == "updated"
    data = json.loads(p.read_text())
    assert data["model"] == "x" and data["permissions"] == mine["permissions"]
    cmds = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert cmds == ["my-linter", "agentmesh hook pre-edit"]
    assert enforcement.install_claude_hooks(tmp_path, remove=True) == "updated"
    assert json.loads(p.read_text())["hooks"] == mine["hooks"]


def test_install_never_overwrites_invalid_json(tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text("{ not json")
    assert enforcement.install_claude_hooks(tmp_path) == "kept" and p.read_text() == "{ not json"


def test_init_installs_hooks_unless_enforcement_off(flutter_only, tmp_path):
    rep = init_with(flutter_only, mock_registry({"w1": "SUCCESS"}))
    assert rep.actions[".claude/settings.json"] == "created"
    assert init_with(flutter_only, mock_registry({"w1": "SUCCESS"})).actions[".claude/settings.json"] == "unchanged"
    from agentmesh.health import run_doctor
    from agentmesh import discovery
    cfg = ProjectConfig.load(flutter_only)
    reg = mock_registry({"w1": "SUCCESS"})
    sections, _ = run_doctor(flutter_only, reg, discovery.current(reg))
    assert any(c.label == "claude hooks" and c.status == "READY" for s in sections for c in s.checks)
    off = tmp_path / "off"
    write(off, "pubspec.yaml", "name: x\ndependencies:\n  flutter:\n    sdk: flutter\n")
    init_with(off, mock_registry({"w1": "SUCCESS"}))
    p = off / ".agentmesh" / "project.yaml"
    d = load_yaml(p); d["enforcement"]["mode"] = "off"; p.write_text(dump_yaml(d))
    assert ".claude/settings.json" not in init_with(off, mock_registry({"w1": "SUCCESS"})).actions or True


def test_dotfile_patterns_match_at_the_repo_root():
    from agentmesh.roles import GLOBAL_FORBIDDEN
    from agentmesh.scope import matches
    for path in (".env", ".env.local", ".git/config", ".agentmesh/project.yaml", "lib/.env", "a/secrets/k.txt", "x/key.pem"):
        assert matches(path, GLOBAL_FORBIDDEN), path
    assert not matches("environment.dart", GLOBAL_FORBIDDEN) and not matches("lib/env_config.dart", GLOBAL_FORBIDDEN)
    assert matches("./lib/a.dart", ["./lib/**"])
