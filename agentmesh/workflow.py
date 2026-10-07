"""Conditional workflow: generation of workflow.yaml and deterministic task planning from it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SIGNALS: dict[str, list[str]] = {
    "api": ["api", "endpoint", "route", "contract", "schema", "payload", "graphql", "openapi", "dto", "rest ", "webhook"],
    "backend": ["backend", "server", "database", "db ", "migration", "service", "controller", "query", "cron", "queue", "worker job"],
    "app": ["screen", "widget", "flutter", "mobile", "navigation", "app bar", "bottom sheet", "ios", "android", "page in the app", "dart"],
    "web": ["web page", "frontend", "browser", "css", "react", "vue", "next", "html", "component library", "landing"],
    "security": ["auth", "login", "logout", "token", "password", "encrypt", "permission", "oauth", "session", "secret", "biometric", "crypto", "jwt"],
    "financial": ["payment", "billing", "invoice", "checkout", "wallet", "ledger", "refund", "payout", "subscription", "kyc"],
    "trivial": ["typo", "rename", "copy change", "wording", "label", "colour", "color", "padding", "margin", "spacing", "icon", "text change", "tooltip"],
    "feature": ["implement", "add ", "build", "create", "new feature", "support for", "introduce", "integrate"],
}
MULTI_AREA = ("api", "backend", "app", "web")


def generate_workflow(project_name: str, kind: str, roles: list[str]) -> dict[str, Any]:
    has = set(roles).__contains__
    impl = [r for r in ("backend-engineer", "app-engineer", "frontend-engineer", "software-engineer") if has(r)]
    sig_for = {"backend-engineer": ["backend", "api"], "app-engineer": ["app"],
               "frontend-engineer": ["web"], "software-engineer": ["feature", "backend", "app", "web", "api"]}
    stages: list[dict[str, Any]] = []
    if has("spec-analyst"):
        stages.append({"id": "spec", "role": "spec-analyst", "when": {"any_of": ["feature", "multi_area", "ambiguous"]},
                       "skip_when": ["trivial"], "purpose": "Pin down requirements and acceptance criteria."})
    if has("contract-keeper"):
        stages.append({"id": "contract", "role": "contract-keeper", "when": {"any_of": ["api"]},
                       "purpose": "Update API/data contracts before implementations."})
    for r in impl:
        stages.append({"id": f"implement-{r.removesuffix('-engineer')}", "role": r, "when": {"any_of": sig_for[r]},
                       "kind": "implementation", "purpose": "Make the change in isolation (git worktree)."})
    stages.append({"id": "test", "role": "test-executor", "after": "implementation",
                   "purpose": "Run verification commands against the implemented worktree."})
    stages.append({"id": "triage", "role": "failure-triage", "on_failure_of": ["test", "review"],
                   "purpose": "Diagnose; produce a focused correction brief for the owning role."})
    stages.append({"id": "review", "role": "reviewer", "always": True,
                   "purpose": "Review the real diff before the manager accepts it."})
    for r, sig in (("security-reviewer", "security"), ("compliance-reviewer", "financial"), ("threat-reviewer", "financial")):
        if has(r):
            stages.append({"id": r.removesuffix("-reviewer") + "-review", "role": r, "when": {"any_of": [sig]},
                           "purpose": "Specialist review when the change touches this area."})
    stages.append({"id": "manager-verify", "actor": "manager", "always": True,
                   "purpose": "Inspect git diff, run `agentmesh verify`, only then accept/integrate."})
    return {
        "schema": 1, "project": project_name, "kind": kind,
        "principle": "Roles are fixed by this file; workers are chosen per run by the AgentMesh router.",
        "stages": stages,
        "signals": SIGNALS,
        "limits": {"max_correction_rounds": 2, "max_roles_per_task": 8},
        "examples": {
            "simple UI bug": ["app-engineer", "test-executor", "reviewer"],
            "backend API feature": ["spec-analyst", "contract-keeper", "backend-engineer", "test-executor", "reviewer"],
        },
    }


@dataclass
class PlanStage:
    id: str
    role: str | None
    actor: str = "worker"
    conditional: bool = False        # decided at runtime (e.g. triage only on failure)
    reason: str = ""


@dataclass
class Plan:
    task: str
    signals: list[str] = field(default_factory=list)
    stages: list[PlanStage] = field(default_factory=list)
    needs_clarification: str | None = None

    def roles(self) -> list[str]:
        return [s.role for s in self.stages if s.role and not s.conditional]

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "signals": self.signals, "needs_clarification": self.needs_clarification,
                "stages": [s.__dict__ for s in self.stages]}


def detect_signals(text: str, vocab: dict[str, list[str]], extra: list[str] | None = None) -> list[str]:
    low = f" {text.lower()} "
    found = {name for name, words in vocab.items() if any(w in low for w in words)}
    found |= set(extra or [])
    areas = found & set(MULTI_AREA)
    if len(areas) >= 2:
        found.add("multi_area")
    if not areas and "trivial" not in found or len(text.split()) > 40:
        found.add("ambiguous")
    return sorted(found)


def plan_task(workflow: dict[str, Any], text: str, extra_signals: list[str] | None = None) -> Plan:
    signals = detect_signals(text, workflow.get("signals", SIGNALS), extra_signals)
    sigset = set(signals)
    plan = Plan(text, signals)
    stages = workflow["stages"]
    impl = [s for s in stages if s.get("kind") == "implementation"]
    chosen_impl = [s["id"] for s in impl if sigset & set(s["when"]["any_of"])]
    if not chosen_impl:
        if len(impl) == 1:
            chosen_impl = [impl[0]["id"]]
        else:
            plan.needs_clarification = ("could not tell which area this touches; candidates: "
                                        + ", ".join(s["role"] for s in impl) + ". Re-run with --signal app|backend|web.")
    # a backend-only API change must not drag in unrelated clients
    for s in stages:
        sid = s["id"]
        if s.get("actor") == "manager":
            plan.stages.append(PlanStage(sid, None, "manager", reason=s["purpose"]))
        elif s.get("kind") == "implementation":
            if sid in chosen_impl:
                plan.stages.append(PlanStage(sid, s["role"], reason=f"signals: {', '.join(sorted(sigset & set(s['when']['any_of']))) or 'only candidate'}"))
        elif s.get("on_failure_of"):
            plan.stages.append(PlanStage(sid, s["role"], conditional=True, reason="only if " + "/".join(s["on_failure_of"]) + " fails"))
        elif s.get("after") == "implementation":
            if chosen_impl:
                plan.stages.append(PlanStage(sid, s["role"], reason="verify what was implemented"))
        elif s.get("always"):
            plan.stages.append(PlanStage(sid, s["role"], reason="always"))
        else:
            hit = sigset & set(s["when"]["any_of"])
            if hit and not (sigset & set(s.get("skip_when", []))):
                plan.stages.append(PlanStage(sid, s["role"], reason=f"signals: {', '.join(sorted(hit))}"))
    # keep declared order but put review stages before manager-verify (already by stage order)
    return plan
