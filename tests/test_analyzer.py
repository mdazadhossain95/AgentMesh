from conftest import commit_all, git, write
from agentmesh.project_analyzer import _has_human_content, analyze, apply_answers


def test_flutter_only(flutter_only):
    p = analyze(flutter_only)
    assert p.kind == "flutter" and p.has_flutter and not p.has_backend
    c = p.components[0]
    assert c.stack == "flutter" and c.details["platforms"] == ["android", "ios"]
    assert c.details["state_management"] == ["riverpod"] and c.details["routing"] == ["go_router"]
    assert c.details["has_l10n"] is True
    assert [v["name"] for v in c.verification] == ["format", "analyze", "test"]


def test_flutter_does_not_assume_a_state_manager(tmp_path):
    write(tmp_path, "pubspec.yaml", "name: x\ndependencies:\n  flutter:\n    sdk: flutter\n")
    write(tmp_path, "lib/main.dart")
    c = analyze(tmp_path).components[0]
    assert "none detected" in c.details["state_management"][0]
    assert not any(k in c.details["state_management"] for k in ("riverpod", "bloc", "provider"))


def test_flutter_plus_backend_is_fullstack(flutter_backend):
    p = analyze(flutter_backend)
    assert p.kind == "full-stack"
    assert {(c.path, c.stack) for c in p.components} == {(".", "flutter"), ("backend", "python")}
    assert p.contracts == ["docs/openapi.yaml"] and p.instruction_files["CLAUDE.md"] is True
    back = next(c for c in p.components if c.path == "backend")
    assert back.frameworks == ["fastapi"] and "alembic" in back.details["database"]


def test_backend_only_and_web(tmp_path):
    write(tmp_path / "b", "requirements.txt", "django\npytest\n")
    assert analyze(tmp_path / "b").kind == "backend"
    write(tmp_path / "w", "package.json", '{"dependencies":{"react":"18","vite":"5"},"scripts":{"test":"vitest","build":"vite build"}}')
    w = analyze(tmp_path / "w")
    assert w.kind == "web" and w.components[0].details["package_manager"] == "npm"
    assert [v["name"] for v in w.components[0].verification] == ["test", "build"]


def test_monorepo_by_workspace_markers(tmp_path):
    write(tmp_path, "pnpm-workspace.yaml", "packages: ['apps/*']\n")
    write(tmp_path, "apps/web/package.json", '{"dependencies":{"next":"14","react":"18"}}')
    write(tmp_path, "apps/api/package.json", '{"dependencies":{"express":"4"}}')
    write(tmp_path, "apps/mobile/package.json", '{"dependencies":{"expo":"50"}}')
    p = analyze(tmp_path)
    assert p.kind == "monorepo" and {c.type for c in p.components} == {"web", "backend", "mobile"}


def test_generic_asks_kind_question(tmp_path):
    write(tmp_path, "notes.txt", "x")
    p = analyze(tmp_path)
    assert p.kind == "generic" and [q.id for q in p.questions] == ["project_kind"]
    apply_answers(p, {"project_kind": "web"})
    assert p.kind == "web"


def test_only_unresolvable_things_are_asked(flutter_backend):
    assert analyze(flutter_backend).questions == []      # everything derivable from the repo


def test_unknown_backend_dir_question(tmp_path):
    write(tmp_path, "pubspec.yaml", "name: x\ndependencies:\n  flutter:\n    sdk: flutter\n")
    write(tmp_path, "server/main.rb", "puts 1")
    p = analyze(tmp_path)
    assert [q.id for q in p.questions] == ["backend_dir"] and p.kind == "full-stack"
    apply_answers(p, {"backend_dir": "no"})
    assert p.kind == "flutter" and not p.has_backend


def test_sensitivity_gating(flutter_only, flutter_backend, tmp_path):
    assert not analyze(flutter_only).security_review
    p = analyze(flutter_backend)
    assert p.security_review and "security" in p.sensitive and not p.compliance_review
    write(tmp_path, "pubspec.yaml", "name: x\ndependencies:\n  flutter:\n    sdk: flutter\n  flutter_stripe: 1\n")
    write(tmp_path, "lib/payments/ledger.dart")
    pay = analyze(tmp_path)
    assert pay.security_review and pay.compliance_review


def test_ignored_dirs_are_not_scanned(tmp_path):
    write(tmp_path, "node_modules/x/package.json", '{"dependencies":{"react":"1"}}')
    assert analyze(tmp_path).components == []


def test_managed_block_alone_is_not_human_content(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text("<!-- AGENTMESH:BEGIN x -->\nstuff\n<!-- AGENTMESH:END -->\n")
    assert not _has_human_content(f)
    f.write_text(f.read_text() + "mine\n")
    assert _has_human_content(f)
