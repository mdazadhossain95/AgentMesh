"""In-process fake worker for tests and offline demos. Never calls a network service."""
from __future__ import annotations

import json
from pathlib import Path

from ..errors import ErrorCode
from ..models import AgentInfo, WorkerState, utcnow
from .base import AgentAdapter, CommandSpec, Normalized, RawResult, RunContext

BEHAVIORS = ("SUCCESS", "QUOTA_EXCEEDED", "RATE_LIMITED", "TIMEOUT", "AUTH_FAILED",
             "CLI_NOT_FOUND", "GENERIC_FAILURE", "NO_CHANGES")

_STDERR = {
    "QUOTA_EXCEEDED": "Error: You have exceeded your current quota. Usage limit reached.",
    "RATE_LIMITED": "Error 429: Too many requests, rate limit hit",
    "AUTH_FAILED": "Error 401: unauthorized. Please log in.",
    "GENERIC_FAILURE": "something broke in an unrecognised way",
}


class MockAdapter(AgentAdapter):
    """behavior: one behavior, or a list consumed one per execute() call (last repeats)."""

    def __init__(self, name: str, behavior: str | list[str] = "SUCCESS", writes: dict[str, str] | None = None,
                 report: dict | None = None):
        super().__init__()
        self.name = name
        self.display_name = f"Mock {name}"
        self.executables = (name,)
        self.behaviors = [behavior] if isinstance(behavior, str) else list(behavior)
        self.writes = writes if writes is not None else {"mock_output.txt": "hello from mock\n"}
        self.report = report                  # override the self-report (to test distrust of worker claims)
        self.calls: list[CommandSpec] = []

    def detect(self, path_env: str | None = None) -> Path | None:
        return Path(f"/mock/bin/{self.name}")

    def version(self, exe: Path, path_env: str | None = None) -> str | None:
        return "mock-1.0"

    def health_check(self, path_env: str | None = None) -> AgentInfo:
        return AgentInfo(name=self.name, display_name=self.display_name, adapter="MockAdapter", installed=True,
                         executable=self.name, path=f"/mock/bin/{self.name}", version="mock-1.0",
                         state=WorkerState.READY.value, headless="YES", structured_output="YES",
                         session_continue="YES", cwd_behavior="PROCESS_CWD", notes=["mock worker"],
                         probed_at=utcnow())

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        return CommandSpec(argv=[self.name, "--mock"], cwd=ctx.cwd, timeout=ctx.timeout, stdin=ctx.prompt, env=ctx.env)

    def execute(self, spec: CommandSpec, run_id: str = "run") -> RawResult:
        self.calls.append(spec)
        behavior = self.behaviors.pop(0) if len(self.behaviors) > 1 else self.behaviors[0]
        raw = RawResult(exit_code=0, started_at=utcnow(), finished_at=utcnow())
        if behavior == "CLI_NOT_FOUND":
            raw.not_found, raw.exit_code = True, None
        elif behavior == "TIMEOUT":
            raw.timed_out, raw.exit_code = True, None
        elif behavior in _STDERR:
            raw.exit_code, raw.stderr = 1, _STDERR[behavior]
        else:
            if behavior == "SUCCESS":
                for rel, content in self.writes.items():
                    target = spec.cwd / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
            files = list(self.writes) if behavior == "SUCCESS" else []
            report = {"status": "done", "summary": "mock work finished", "files_changed": files, "tests": [], "notes": ""}
            report.update(self.report or {})
            raw.stdout = f"Done.\n```agentmesh-report\n{json.dumps(report)}\n```"
        return raw

    def normalize_result(self, raw: RawResult) -> Normalized:
        return Normalized(summary=raw.stdout.strip() or raw.stderr.strip())

    def classify_error(self, raw: RawResult, norm: Normalized) -> ErrorCode | None:
        return super().classify_error(raw, norm)
