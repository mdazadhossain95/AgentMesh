import pytest

from agentmesh.project_analyzer import analyze
from agentmesh.roles import render_role, role_policy, select_roles
from agentmesh.workflow import generate_workflow, plan_task

SECTIONS = ["Purpose", "When This Role Is Used", "Read Before Work", "Responsibilities", "Allowed Changes",
            "Must Not Change", "Project Rules", "Required Verification", "Expected Output", "Report Back Format",
            "Handoff Contract"]


def test_roles_flutter_only(flutter_only):
    assert select_roles(analyze(flutter_only)) == ["project-analyst", "spec-analyst", "app-engineer", "test-executor",
                                                   "failure-triage", "reviewer"]


def test_roles_flutter_backend(flutter_backend):
    roles = select_roles(analyze(flutter_backend))
    assert roles[:6] == ["project-analyst", "spec-analyst", "contract-keeper", "backend-engineer", "app-engineer", "test-executor"]
    assert "security-reviewer" in roles and "compliance-reviewer" not in roles


def test_roles_backend_only_and_generic(tmp_path):
    (tmp_path / "b").mkdir(); (tmp_path / "b" / "requirements.txt").write_text("flask\n")
    assert select_roles(analyze(tmp_path / "b")) == ["project-analyst", "spec-analyst", "backend-engineer", "test-executor",
                                                     "failure-triage", "reviewer"]
    (tmp_path / "g").mkdir()
    r = select_roles(analyze(tmp_path / "g"))
    assert "software-engineer" in r and not any(x in r for x in ("app-engineer", "backend-engineer", "contract-keeper"))


def test_financial_project_gets_specialist_reviewers(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: x\ndependencies:\n  flutter:\n    sdk: flutter\n  flutter_stripe: 1\n")
    (tmp_path / "lib" / "payments").mkdir(parents=True)
    (tmp_path / "lib" / "payments" / "ledger.dart").write_text("")
    (tmp_path / "backend").mkdir(); (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    r = select_roles(analyze(tmp_path))
    assert {"security-reviewer", "compliance-reviewer", "threat-reviewer"} <= set(r)


@pytest.mark.parametrize("role", ["project-analyst", "spec-analyst", "contract-keeper", "backend-engineer", "app-engineer",
                                  "test-executor", "failure-triage", "reviewer", "security-reviewer"])
def test_every_role_has_all_sections(flutter_backend, role):
    text = render_role(role, analyze(flutter_backend))
    assert text.startswith("# Role:")
    for sec in SECTIONS:
        assert f"## {sec}" in text, (role, sec)
    assert "agentmesh-report" in text


def test_flutter_rules_come_from_the_repo_not_from_assumptions(flutter_only):
    text = render_role("app-engineer", analyze(flutter_only))
    assert "riverpod" in text and "go_router" in text and "flutter analyze" in text and "dart format" in text
    assert "Bloc" not in text and "gold" not in text.lower()


def test_scope_policy_is_narrow(flutter_backend):
    p = analyze(flutter_backend)
    app, back = role_policy("app-engineer", p), role_policy("backend-engineer", p)
    assert "lib/**" in app["allowed_paths"] and not any(a.startswith("backend") for a in app["allowed_paths"])
    assert back["allowed_paths"] == ["backend/**"]
    assert "**/openapi*.yaml" in back["forbidden_paths"]            # contract-keeper owns contracts
    assert role_policy("reviewer", p)["capability"] == "read-only" and role_policy("reviewer", p)["allowed_paths"] == []
    assert ".env" in app["forbidden_paths"]


def test_workflow_is_conditional(flutter_backend):
    roles = select_roles(analyze(flutter_backend))
    wf = generate_workflow("GoVio", "full-stack", roles)
    ui = plan_task(wf, "Fix padding on the profile screen")
    assert ui.roles() == ["app-engineer", "test-executor", "reviewer"]
    api = plan_task(wf, "Add a new endpoint for the booking API")
    assert api.roles() == ["spec-analyst", "contract-keeper", "backend-engineer", "test-executor", "reviewer"]
    both = plan_task(wf, "Implement the profile API and Flutter profile screen")
    assert both.roles() == ["spec-analyst", "contract-keeper", "backend-engineer", "app-engineer", "test-executor", "reviewer"]
    assert any(s.role == "failure-triage" and s.conditional for s in ui.stages)      # triage only on failure
    assert ui.stages[-1].actor == "manager"


def test_security_review_only_when_signalled(flutter_backend):
    wf = generate_workflow("x", "full-stack", select_roles(analyze(flutter_backend)))
    assert "security-reviewer" in plan_task(wf, "fix login token refresh in the app screen").roles()
    assert "security-reviewer" not in plan_task(wf, "fix padding on the screen").roles()


def test_ambiguous_task_asks_for_clarification(flutter_backend):
    wf = generate_workflow("x", "full-stack", select_roles(analyze(flutter_backend)))
    assert plan_task(wf, "make it better").needs_clarification
    assert plan_task(wf, "make it better", ["app"]).needs_clarification is None


def test_single_engineer_project_never_needs_clarification(flutter_only):
    wf = generate_workflow("x", "flutter", select_roles(analyze(flutter_only)))
    p = plan_task(wf, "do the thing")
    assert p.needs_clarification is None and "app-engineer" in p.roles()
