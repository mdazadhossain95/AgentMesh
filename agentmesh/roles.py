"""Project roles: selection from a ProjectProfile and rendering of role Markdown.

Methodology borrowed from role-oriented agent packs: one narrow file per role, explicit read-before-work,
explicit write scope, explicit prohibitions, mandatory verification, structured report-back, handoff contract.
No domain rules are copied; every project rule below is derived from what the analyzer found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .project_analyzer import Component, ProjectProfile

ENGINEERS = ("app-engineer", "frontend-engineer", "backend-engineer", "software-engineer")
GLOBAL_FORBIDDEN = [".git/**", ".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "**/*.p12", "**/*.jks",
                    "**/*.keystore", "**/secrets/**", ".agentmesh/**", ".agentmesh-worktrees/**"]
CONTRACT_GLOBS = ["**/openapi*.yaml", "**/openapi*.yml", "**/openapi*.json", "**/swagger*.json", "**/swagger*.yaml",
                  "**/*.proto", "**/*.graphql", "docs/api/**", "contracts/**"]


def select_roles(p: ProjectProfile) -> list[str]:
    roles = ["project-analyst"]
    engineers = []
    if p.has_mobile: engineers.append("app-engineer")
    if p.has_web: engineers.append("frontend-engineer")
    if p.has_backend: engineers.append("backend-engineer")
    if not engineers:
        engineers.append("software-engineer")
    roles.append("spec-analyst")
    if p.has_backend and (p.has_mobile or p.has_web or p.contracts):
        roles.append("contract-keeper")
    # backend first: contracts flow backend -> clients
    order = {"backend-engineer": 0, "app-engineer": 1, "frontend-engineer": 2, "software-engineer": 3}
    roles += sorted(engineers, key=order.get)
    roles += ["test-executor", "failure-triage", "reviewer"]
    if p.security_review: roles.append("security-reviewer")
    if p.compliance_review: roles.append("compliance-reviewer")
    if p.threat_review: roles.append("threat-reviewer")
    return roles


@dataclass
class RoleSpec:
    role: str
    title: str
    capability: str                     # read-only | write
    purpose: str
    when: list[str]
    responsibilities: list[str]
    allowed: list[str]
    allowed_paths: list[str]
    must_not: list[str]
    outputs: list[str]
    handoff_to: list[str] = field(default_factory=list)
    isolation: str = "worktree"
    verify: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)


def _paths(comps: list[Component]) -> list[str]:
    out: list[str] = []
    for c in comps:
        pre = "" if c.path == "." else c.path + "/"
        if c.stack == "flutter":
            out += [f"{pre}lib/**", f"{pre}test/**", f"{pre}assets/**", f"{pre}l10n/**", f"{pre}pubspec.yaml", f"{pre}pubspec.lock"]
        else:
            out.append(f"{pre}**")
    return out


def _verify(comps: list[Component]) -> list[str]:
    cmds = []
    for c in comps:
        for v in c.verification:
            cmds.append(f"`{v['run']}` (in `{v['cwd']}`)")
    return cmds


def _rules(p: ProjectProfile, comps: list[Component]) -> list[str]:
    r: list[str] = []
    for c in comps:
        d = c.details
        if c.stack == "flutter":
            sm = ", ".join(d.get("state_management", []))
            r += [
                f"Respect the existing architecture of `{c.path}`; read neighbouring features before adding code. "
                f"Top-level lib dirs: {', '.join(d.get('top_level_lib_dirs', [])) or '(none)'}.",
                f"State management detected: {sm}. Use it; do not introduce a second approach.",
                f"Routing detected: {', '.join(d.get('routing', []))}. Add routes the same way existing ones are added.",
                "Reuse existing widgets/components/theme tokens before creating new ones.",
                "Keep business logic where this project already keeps it; do not move logic into widgets or vice versa.",
                "Do not add dependencies unless the task requires it; if you must, say why in the report.",
            ]
            if d.get("has_l10n"):
                r.append("Localization is enabled: no hard-coded user-facing strings; add keys to every ARB/locale file and regenerate.")
            if d.get("codegen"):
                r.append(f"Generated code ({', '.join(d['codegen'])}): edit sources, re-run the generator, never hand-edit generated files.")
            r.append("Run `dart format` on changed Dart files, then `flutter analyze`, then the relevant `flutter test` targets.")
            r.append("Do not edit `android/` or `ios/` platform code unless the task says so explicitly.")
        elif c.stack == "node":
            r.append(f"`{c.path}`: {', '.join(c.frameworks) or 'node'} ({d.get('package_manager')}); "
                     f"{'TypeScript' if d.get('typescript') else 'JavaScript'}. Use existing scripts: {', '.join(d.get('scripts', [])[:8])}.")
        elif c.stack == "python":
            r.append(f"`{c.path}`: Python {', '.join(c.frameworks) or 'project'}; database layer: {', '.join(d.get('database', [])) or 'none detected'}.")
        elif c.stack != "unknown":
            r.append(f"`{c.path}`: {c.stack} {', '.join(c.frameworks)}".strip())
        if c.type == "backend":
            r += ["Follow the existing layering (routes/controllers -> services -> data access); do not bypass it.",
                  "Never edit already-applied migrations; add a new one.",
                  "Do not weaken authentication/authorization; do not log credentials or tokens."]
    if p.sensitive:
        r.append("Security-sensitive areas exist (" + ", ".join(f"{g}: {', '.join(v[:3])}" for g, v in p.sensitive.items())
                 + "). Touch them only when the task requires it and flag every such change in the report.")
    humans = [f for f, ok in p.instruction_files.items() if ok]
    if humans:
        r.append(f"Human-written instructions in {', '.join(humans)} take precedence over this file when they conflict.")
    return r


def spec_for(role: str, p: ProjectProfile) -> RoleSpec:
    mobile, web, back = p.of_type("mobile"), p.of_type("web"), p.of_type("backend")
    allc = p.components
    have = set(select_roles(p))
    nxt = lambda *names: [n for n in names if n in have]
    impl = nxt("backend-engineer", "app-engineer", "frontend-engineer", "software-engineer")

    def eng(title: str, comps: list[Component], purpose: str, extra_forbid: list[str] | None = None) -> RoleSpec:
        return RoleSpec(
            role, title, "write", purpose,
            when=["The task changes behaviour inside this role's code area.", "A focused correction is requested after failed tests or review."],
            responsibilities=["Implement exactly the task's requirements and acceptance criteria.",
                              "Add or update tests alongside the code when the project has a test setup.",
                              "Follow existing patterns found in neighbouring code.",
                              "Keep the change minimal; list anything you noticed but did not change."],
            allowed=["Source, test and asset files in: " + ", ".join(f"`{c.path}`" for c in comps)],
            allowed_paths=_paths(comps),
            must_not=["Files outside the allowed scope (the manager checks this from git).",
                      "Contract/API definitions, unless this role is the contract owner.",
                      "Unrelated refactors, formatting churn, dependency upgrades.",
                      "Secrets, `.env*`, signing keys, `.agentmesh/`.", "Other agents or CLIs: you do not delegate."]
            + (extra_forbid or []),
            outputs=["Code and test changes in your workspace.", "Report with changed files, commands run, and results."],
            handoff_to=nxt("test-executor"), verify=_verify(comps), rules=_rules(p, comps))

    if role == "project-analyst":
        return RoleSpec(role, "Project Analyst", "read-only",
            "Understand the repository and keep AgentMesh's picture of it accurate.",
            ["First contact with a repository.", "The structure changed (new package, backend, CI).", "Role scope or verification commands look wrong."],
            ["Classify the project and list its components.", "Identify architecture, conventions, source-of-truth docs, tests, CI.",
             "Find implementation boundaries and security-sensitive areas.", "Recommend roles to add or remove; list missing information.",
             "Propose updates to `.agentmesh/project.yaml` (overrides section) when detection is wrong."],
            ["Read any file.", "Edit `.agentmesh/project.yaml` overrides and `.agentmesh/references/` notes."],
            [".agentmesh/project.yaml"],
            ["Product source, tests, configs, lockfiles.", "Implementing features or fixing bugs.", "Running paid or destructive commands."],
            ["Project summary: kind, components, boundaries.", "Role recommendations and open questions."],
            handoff_to=nxt("spec-analyst"), isolation="inplace", rules=_rules(p, allc))
    if role == "spec-analyst":
        return RoleSpec(role, "Spec Analyst", "read-only",
            "Turn a request into precise, testable requirements before code is written.",
            ["The request is ambiguous, large, or touches several components.", "Skip for trivial, well-specified fixes."],
            ["Read relevant docs and existing code to ground the request.", "State requirements, edge cases, non-goals and acceptance criteria.",
             "Name which roles must act and in what order.", "List open questions the manager must resolve with the user."],
            ["Read any file."], [], ["Any file changes.", "Designing UI pixel details or writing implementation code."],
            ["Requirements list.", "Acceptance criteria (testable).", "Affected components/files.", "Open questions."],
            handoff_to=nxt("contract-keeper") + impl, isolation="inplace", rules=_rules(p, allc))
    if role == "contract-keeper":
        return RoleSpec(role, "Contract Keeper", "write",
            "Own API/data contracts between components so clients and backend stay compatible.",
            ["An endpoint, payload, schema, or event is added or changed.", "Skip when no contract is touched."],
            ["Update contract definitions first; keep them the single source of truth.", "Flag breaking changes and versioning needs.",
             "Provide exact request/response shapes for implementers."],
            ["Contract files: " + (", ".join(f"`{c}`" for c in p.contracts) or "none exist yet; create under `docs/api/` or `contracts/` as the team prefers")],
            CONTRACT_GLOBS, ["Implementation code in clients or backend.", "Secrets or real user data in examples."],
            ["Updated contract files.", "Summary of breaking vs additive changes."],
            handoff_to=nxt("backend-engineer", "app-engineer", "frontend-engineer"),
            verify=[f"Contract files parse (YAML/JSON/proto) and match the shapes in the task."] ,
            rules=_rules(p, back))
    if role == "backend-engineer":
        return eng("Backend Engineer", back, "Implement and fix server-side behaviour.",
                   ["Client code (app/frontend)."] if (mobile or web) else None)
    if role == "app-engineer":
        return eng("App Engineer", mobile, "Implement and fix mobile app behaviour and UI.",
                   ["Backend code."] if back else None)
    if role == "frontend-engineer":
        return eng("Frontend Engineer", web, "Implement and fix web frontend behaviour and UI.",
                   ["Backend code."] if back else None)
    if role == "software-engineer":
        return eng("Software Engineer", allc, "Implement and fix behaviour in repositories without a recognised app/backend split.")
    if role == "test-executor":
        return RoleSpec(role, "Test Executor", "read-only",
            "Run the project's verification commands against a workspace and report facts.",
            ["After any implementation role finishes.", "Before review."],
            ["Run each required command; capture exit codes and the failing output.", "Report pass/fail per command, never guess results.",
             "If a command cannot run (missing tool, permission denied), say BLOCKED and why."],
            ["Run test/analyze/format-check commands.", "Build artifacts that git ignores."], [],
            ["Editing source or tests to make them pass.", "Skipping or disabling tests.", "Network or paid operations beyond the test commands."],
            ["Per-command result table.", "First failing assertion/stack trace for each failure."],
            handoff_to=nxt("reviewer", "failure-triage"), isolation="worktree", verify=_verify(allc), rules=_rules(p, allc))
    if role == "failure-triage":
        return RoleSpec(role, "Failure Triage", "read-only",
            "Diagnose why verification failed and point the fix at the right role.",
            ["Only after a failing test, analyzer, build or review finding."],
            ["Reproduce or read the failure output.", "Identify root cause with file/line evidence.",
             "Decide: product bug, test bug, environment issue, or contract mismatch.", "Write a focused correction brief for the owning role."],
            ["Read code and logs; run read-only diagnostic commands."], [],
            ["Fixing the code.", "Weakening or deleting tests to get green."],
            ["Root cause with evidence.", "Owning role.", "Correction brief: exactly what to change, what not to touch."],
            handoff_to=impl, isolation="worktree", rules=_rules(p, allc))
    if role == "reviewer":
        return RoleSpec(role, "Reviewer", "read-only",
            "Review the actual diff for correctness, scope, and fit with the codebase.",
            ["After tests pass, before the manager accepts the work."],
            ["Read the diff, not the worker's summary.", "Check requirements and acceptance criteria one by one.",
             "Look for scope creep, missing tests, broken conventions, risky changes.", "Rate each finding: blocker / should-fix / nit."],
            ["Read any file and the task branch diff."], [], ["Editing files.", "Approving without reading the diff."],
            ["Verdict: approve / changes-requested.", "Findings with file:line, severity, suggested fix."],
            handoff_to=nxt("security-reviewer", "compliance-reviewer", "threat-reviewer"), isolation="worktree",
            rules=_rules(p, allc))
    if role == "security-reviewer":
        areas = sum(p.sensitive.values(), [])[:6]
        return RoleSpec(role, "Security Reviewer", "read-only",
            "Review changes that touch authentication, secrets, crypto, input handling or permissions.",
            ["The diff touches security-sensitive areas: " + (", ".join(areas) or "(see project.yaml)"), "Skip otherwise."],
            ["Check authn/authz, input validation, injection, secret handling, insecure storage/transport, logging of sensitive data.",
             "Check new dependencies for risk.", "Report only issues with a concrete exploit or weakness path."],
            ["Read any file and the diff."], [], ["Editing files."],
            ["Verdict.", "Findings with severity, evidence, remediation."], isolation="worktree", rules=_rules(p, allc))
    if role == "compliance-reviewer":
        return RoleSpec(role, "Compliance Reviewer", "read-only",
            "Check changes against the compliance obligations this project actually declares.",
            ["Money movement, personal data, consent, or audit-relevant behaviour changes."],
            ["Identify obligations from project docs (do not invent regulations).", "Check data retention, consent, auditability, money-handling invariants.",
             "Say clearly when a rule's source is missing instead of guessing."],
            ["Read any file and the diff."], [], ["Editing files.", "Inventing legal requirements."],
            ["Verdict.", "Findings tied to a named source document."], isolation="worktree", rules=_rules(p, allc))
    if role == "threat-reviewer":
        return RoleSpec(role, "Threat Reviewer", "read-only",
            "Model abuse cases for money- or data-handling backend changes.",
            ["New or changed endpoints, payment flows, or privilege boundaries."],
            ["List assets, actors, trust boundaries touched.", "Enumerate abuse cases (replay, race, tampering, enumeration).", "Check mitigations exist in code."],
            ["Read any file and the diff."], [], ["Editing files."],
            ["Threat list with likelihood/impact and missing mitigations."], isolation="worktree", rules=_rules(p, back or allc))
    raise KeyError(role)


REPORT_FORMAT = """\
End the final message with one fenced block (AgentMesh parses it; the manager still verifies against git):

```agentmesh-report
{"status": "done|blocked|failed",
 "summary": "<=5 lines",
 "files_changed": ["relative/path"],
 "tests": [{"command": "...", "result": "pass|fail|not-run", "notes": ""}],
 "notes": "risks, assumptions, follow-ups"}
```"""


def render_role(role: str, p: ProjectProfile, reference_note: str | None = None) -> str:
    s = spec_for(role, p)
    bullets = lambda xs: "\n".join(f"- {x}" for x in xs) if xs else "- (none)"
    reads = ["`.agentmesh/project.yaml` and `.agentmesh/workflow.yaml`", "The task description given to you (requirements, acceptance criteria)"]
    reads += [f"`{f}` (human instructions)" for f, ok in p.instruction_files.items() if ok and f in ("CLAUDE.md", "AGENTS.md")]
    reads += [f"`{d}`" for d in p.docs[:6]]
    if p.contracts and role != "project-analyst":
        reads.append("Contract files: " + ", ".join(f"`{c}`" for c in p.contracts[:4]))
    reads.append("Existing code next to what you will change")
    if reference_note:
        reads.append(reference_note)
    rules = list(s.rules)
    out = [f"# Role: {s.title} (`{role}`)", "",
           "## Purpose", s.purpose, "",
           "## When This Role Is Used", bullets(s.when), "",
           "## Read Before Work", bullets(reads), "",
           "## Responsibilities", bullets(s.responsibilities), "",
           "## Allowed Changes", f"Capability: **{s.capability}**" + ("  \nThis role must not modify files." if s.capability == "read-only" else ""),
           bullets(s.allowed), "",
           "## Must Not Change", bullets(s.must_not), "",
           "## Project Rules", bullets(rules or ["No project-specific rules detected; follow the codebase's existing conventions."]), "",
           "## Required Verification", bullets(s.verify or ["Re-read your own diff; state exactly what you could not verify."]), "",
           "## Expected Output", bullets(s.outputs), "",
           "## Report Back Format", REPORT_FORMAT, "",
           "## Handoff Contract",
           bullets([f"Next role(s) the manager may hand your result to: {', '.join(f'`{n}`' for n in s.handoff_to) or 'manager decides'}.",
                    "You do not hand off yourself and you do not call other agents; the manager routes.",
                    "State precisely what the next role needs: files, commands, open risks."]), ""]
    return "\n".join(out)


def role_policy(role: str, p: ProjectProfile) -> dict[str, Any]:
    """Machine-readable slice stored in project.yaml (enforced by `delegate`)."""
    s = spec_for(role, p)
    forbidden = list(GLOBAL_FORBIDDEN)
    if role == "project-analyst":
        forbidden = [f for f in forbidden if f != ".agentmesh/**"]
    if role in ENGINEERS and "contract-keeper" in select_roles(p):
        forbidden += CONTRACT_GLOBS
    return {"title": s.title, "capability": s.capability, "isolation": s.isolation,
            "allowed_paths": s.allowed_paths if s.capability == "write" else [],
            "forbidden_paths": forbidden, "verification": s.verify, "handoff_to": s.handoff_to}
