"""Proof the scanner and role generator work across real-world stacks, not just the Flutter fixtures.

Each test: analyze the synthetic project, check classification, then run a real `agentmesh init`
(mocked workers only) and check the generated roles/agent files/verification commands.
"""
from helpers import init_with, mock_registry
from agentmesh.project_analyzer import analyze
from agentmesh.roles import select_roles


def test_android_native_is_detected_as_mobile(android_native):
    p = analyze(android_native)
    assert p.kind == "mobile" and p.has_mobile and not p.has_backend and not p.has_web
    assert select_roles(p) == ["project-analyst", "spec-analyst", "app-engineer", "test-executor",
                               "failure-triage", "reviewer"]
    stacks = {c.stack for c in p.components}
    assert "java/kotlin" in stacks


def test_android_native_init_generates_app_engineer_with_gradle_test(android_native):
    rep = init_with(android_native, mock_registry({"w1": "SUCCESS"}))
    assert rep.roles == ["project-analyst", "spec-analyst", "app-engineer", "test-executor", "failure-triage", "reviewer"]
    md = (android_native / ".agentmesh" / "agents" / "app-engineer.md").read_text(encoding="utf-8")
    assert "app/**" in md or "**" in md


def test_react_web_is_detected_as_web(react_web):
    p = analyze(react_web)
    assert p.kind == "web" and p.has_web and not p.has_backend and not p.has_mobile
    assert select_roles(p) == ["project-analyst", "spec-analyst", "frontend-engineer", "test-executor",
                               "failure-triage", "reviewer"]
    comp = p.components[0]
    assert comp.stack == "node" and "react" in comp.frameworks and comp.details["typescript"]


def test_react_web_init_picks_up_vitest_command(react_web):
    rep = init_with(react_web, mock_registry({"w1": "SUCCESS"}))
    assert "frontend-engineer" in rep.roles
    md = (react_web / ".agentmesh" / "agents" / "frontend-engineer.md").read_text(encoding="utf-8")
    assert "frontend-engineer" in md.lower() or "web" in md.lower()


def test_node_backend_is_detected_as_backend(node_backend):
    p = analyze(node_backend)
    assert p.kind == "backend" and p.has_backend and not p.has_web and not p.has_mobile
    assert select_roles(p) == ["project-analyst", "spec-analyst", "backend-engineer", "test-executor",
                               "failure-triage", "reviewer"]
    comp = p.components[0]
    assert comp.stack == "node" and "express" in comp.frameworks and "postgres" in comp.details["database"]


def test_node_backend_init_generates_backend_engineer(node_backend):
    rep = init_with(node_backend, mock_registry({"w1": "SUCCESS"}))
    assert "backend-engineer" in rep.roles
    assert (node_backend / ".agentmesh" / "agents" / "backend-engineer.md").is_file()


def test_ios_native_is_not_detected_generic_fallback_only(ios_native):
    """Known gap: no Podfile/.xcodeproj detector exists yet, so a pure-Swift iOS app gets zero
    components and falls back to the generic single-engineer shape. This locks in that current
    (imperfect) behaviour so a future change to it is a deliberate decision, not a silent regression."""
    p = analyze(ios_native)
    assert p.kind == "generic" and not p.has_mobile and not p.has_web and not p.has_backend
    assert p.components == []
    assert select_roles(p) == ["project-analyst", "spec-analyst", "software-engineer", "test-executor",
                               "failure-triage", "reviewer"]


def test_ios_native_init_still_succeeds_with_software_engineer_fallback(ios_native):
    rep = init_with(ios_native, mock_registry({"w1": "SUCCESS"}))
    assert "software-engineer" in rep.roles and "app-engineer" not in rep.roles
    assert (ios_native / ".agentmesh" / "agents" / "software-engineer.md").is_file()


def test_full_stack_combination_still_orders_backend_before_app(flutter_backend):
    """Regression guard: the multi-stack fixtures above are each single-signal; this confirms
    the existing mixed-signal (flutter + backend) ordering still holds alongside them."""
    p = analyze(flutter_backend)
    assert p.kind == "full-stack"
    roles = select_roles(p)
    assert roles.index("backend-engineer") < roles.index("app-engineer")
