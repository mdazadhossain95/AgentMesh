import hashlib
from pathlib import Path

import pytest

from conftest import commit_all, git, write
from helpers import init_with, mock_registry
from agentmesh import bootstrap
from agentmesh.config import ProjectConfig, dump_yaml, load_yaml

REG = lambda: mock_registry({"w1": "SUCCESS", "w2": "SUCCESS"})


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git/" not in str(p)}


def test_init_creates_project_layer(flutter_backend):
    rep = init_with(flutter_backend, REG())
    mesh = flutter_backend / ".agentmesh"
    for f in ("project.yaml", "workflow.yaml", "README.md"):
        assert (mesh / f).is_file()
    assert {p.stem for p in (mesh / "agents").glob("*.md")} == set(rep.roles)
    for d in ("tasks", "reports", "references", "runtime"):
        assert (mesh / d).is_dir()
    cfg = ProjectConfig.load(flutter_backend)
    assert cfg.kind == "full-stack" and set(cfg.roles()) == set(rep.roles)
    assert cfg.get("routing.preferred_order") == []       # mocks are not in the quality order list
    assert cfg.get("verification.commands")


def test_init_is_idempotent_byte_for_byte(flutter_backend):
    init_with(flutter_backend, REG())
    before = snapshot(flutter_backend)
    rep = init_with(flutter_backend, REG())
    assert snapshot(flutter_backend) == before
    assert set(rep.actions.values()) == {"unchanged"}
    init_with(flutter_backend, REG())
    assert snapshot(flutter_backend) == before


def test_existing_claude_md_is_preserved_and_section_not_duplicated(flutter_backend):
    original = (flutter_backend / "CLAUDE.md").read_text()
    for _ in range(3):
        init_with(flutter_backend, REG())
    text = (flutter_backend / "CLAUDE.md").read_text()
    assert text.startswith(original.rstrip("\n"))
    assert text.count(bootstrap.BEGIN) == 1 and text.count(bootstrap.END) == 1
    assert "agentmesh delegate" in text and ".agentmesh/workflow.yaml" in text
    assert text.count("# Human rules") == 1


def test_human_text_after_managed_block_survives_reinit(flutter_backend):
    init_with(flutter_backend, REG())
    p = flutter_backend / "CLAUDE.md"
    p.write_text(p.read_text() + "\n## My own notes\nkeep me\n")
    init_with(flutter_backend, REG())
    assert "keep me" in p.read_text() and p.read_text().count(bootstrap.BEGIN) == 1


def test_claude_md_created_when_missing(flutter_only):
    assert not (flutter_only / "CLAUDE.md").exists()
    init_with(flutter_only, REG())
    assert bootstrap.BEGIN in (flutter_only / "CLAUDE.md").read_text()
    assert bootstrap.BEGIN in (flutter_only / "AGENTS.md").read_text()


def test_human_edited_role_file_is_never_overwritten(flutter_backend):
    init_with(flutter_backend, REG())
    role = flutter_backend / ".agentmesh" / "agents" / "reviewer.md"
    role.write_text(role.read_text() + "\n- my custom review rule\n")
    # change the repo so the generated text would differ
    write(flutter_backend, "ARCHITECTURE.md", "# arch")
    rep = init_with(flutter_backend, REG())
    assert rep.actions[".agentmesh/agents/reviewer.md"] == "kept" and "my custom review rule" in role.read_text()


def test_unedited_role_files_follow_the_repo(flutter_backend):
    init_with(flutter_backend, REG())
    write(flutter_backend, "ARCHITECTURE.md", "# arch")
    rep = init_with(flutter_backend, REG())
    assert rep.actions[".agentmesh/agents/app-engineer.md"] == "updated"
    assert "ARCHITECTURE.md" in (flutter_backend / ".agentmesh/agents/app-engineer.md").read_text()


def test_human_sections_of_project_yaml_survive_reinit(flutter_backend):
    init_with(flutter_backend, REG())
    p = flutter_backend / ".agentmesh" / "project.yaml"
    data = load_yaml(p)
    data["routing"]["strategy"] = "quality-first"
    data["routing"]["roles"] = {"backend-engineer": ["w2"]}
    data["delegation"]["max_depth"] = 2
    data["overrides"]["roles"] = {"app-engineer": {"allowed_paths": ["lib/features/**"]}}
    p.write_text(dump_yaml(data))
    init_with(flutter_backend, REG(), manager="codex")
    cfg = ProjectConfig.load(flutter_backend)
    assert cfg.get("routing.strategy") == "quality-first" and cfg.get("routing.roles.backend-engineer") == ["w2"]
    assert cfg.get("delegation.max_depth") == 2 and cfg.roles()["app-engineer"]["allowed_paths"] == ["lib/features/**"]
    assert cfg.get("manager.default") == "codex"


def test_gitignore_block_added_once_and_existing_lines_kept(flutter_only):
    write(flutter_only, ".gitignore", "build/\n")
    for _ in range(2):
        init_with(flutter_only, REG())
    gi = (flutter_only / ".gitignore").read_text()
    assert gi.startswith("build/") and gi.count(bootstrap.GI_BEGIN) == 1
    for line in (".agentmesh/runtime/", ".agentmesh-worktrees/", ".agentmesh/state/", ".agentmesh/logs/"):
        assert line in gi


def test_dry_run_writes_nothing(flutter_backend):
    before = snapshot(flutter_backend)
    rep = init_with(flutter_backend, REG(), dry_run=True)
    assert snapshot(flutter_backend) == before and rep.actions[".agentmesh/project.yaml"] == "created"


def test_questions_asked_only_when_needed_and_answers_recorded(tmp_path):
    write(tmp_path, "pubspec.yaml", "name: x\ndependencies:\n  flutter:\n    sdk: flutter\n")
    write(tmp_path, "server/main.rb", "puts 1")
    asked = []
    def asker(text, choices, default):
        asked.append(text); return "yes"
    rep = init_with(tmp_path, REG(), asker=asker)
    assert len(asked) == 1 and "backend" in asked[0] and rep.profile.kind == "full-stack"
    assert "backend-engineer" in rep.roles
    assert ProjectConfig.load(tmp_path).get("project.answers") == {"backend_dir": "yes"}


def test_no_questions_for_derivable_repo(flutter_backend):
    def asker(*a):
        raise AssertionError("must not ask")
    init_with(flutter_backend, REG(), asker=asker)


def test_non_interactive_records_assumptions(tmp_path):
    write(tmp_path, "notes.txt", "x")
    rep = init_with(tmp_path, REG())
    assert rep.profile.kind == "generic" and rep.assumptions and "software-engineer" in rep.roles
    assert ProjectConfig.load(tmp_path).get("project.assumptions")


def test_stale_roles_are_reported_not_deleted(flutter_backend):
    init_with(flutter_backend, REG(), security=True)
    rep = init_with(flutter_backend, REG(), security=False)
    assert "security-reviewer" in rep.stale_roles and (flutter_backend / ".agentmesh/agents/security-reviewer.md").exists()


def test_reference_is_indexed_not_copied(flutter_only, tmp_path):
    ref = tmp_path / "ref"
    write(ref, "agents/backend-engineer.md", "# Backend\n## Gold price rules\nnever do X")
    write(ref, "skills/foo/SKILL.md", "# Foo skill")
    init_with(flutter_only, REG(), reference=str(ref))
    idx = (flutter_only / ".agentmesh/references/index.md").read_text()
    assert "backend-engineer.md" in idx and "foo/SKILL.md" in idx and "do NOT apply" in idx
    roles = "".join(p.read_text() for p in (flutter_only / ".agentmesh/agents").glob("*.md"))
    assert "Gold price rules" not in roles and "never do X" not in roles          # business rules not copied
    assert "references/index.md" in (flutter_only / ".agentmesh/agents/app-engineer.md").read_text()
    assert ProjectConfig.load(flutter_only).get("references") == [str(ref)]


def test_manager_choice_is_recorded_and_gets_instructions(flutter_only):
    init_with(flutter_only, REG(), manager="codex")
    assert bootstrap.BEGIN in (flutter_only / "AGENTS.md").read_text()
    assert ProjectConfig.load(flutter_only).get("manager.default") == "codex"


def test_no_secrets_written_to_project_files(flutter_backend, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-appear-123456")
    init_with(flutter_backend, REG())
    blob = "".join(p.read_text() for p in (flutter_backend / ".agentmesh").rglob("*") if p.is_file())
    assert "sk-should-never" not in blob


@pytest.mark.parametrize("cli,expected", [
    ("claude", "CLAUDE.md"), ("codex", "AGENTS.md"), ("copilot", "AGENTS.md"), ("cline", "AGENTS.md"),
    ("kilo", "AGENTS.md"), ("kiro", "AGENTS.md"), ("antigravity", "AGENTS.md"), ("unknown-cli", "AGENTS.md")])
def test_manager_file_per_cli(cli, expected):
    assert bootstrap.manager_file(cli) == expected


def test_doctor_reports_manager_file(flutter_backend):
    from agentmesh import discovery
    from agentmesh.health import run_doctor
    reg = REG()
    init_with(flutter_backend, reg)
    sections, _ = run_doctor(flutter_backend, reg, discovery.current(reg))
    sec = next(s for s in sections if s.title == "Security & hygiene")
    mgr = next(c for c in sec.checks if c.label.startswith("manager "))
    assert mgr.status == "READY"
    (flutter_backend / "AGENTS.md").write_text("x")
    (flutter_backend / "CLAUDE.md").write_text("x")
    sections, _ = run_doctor(flutter_backend, reg, discovery.current(reg))
    mgr = next(c for s in sections for c in s.checks if c.label.startswith("manager "))
    assert mgr.status == "WARN"
