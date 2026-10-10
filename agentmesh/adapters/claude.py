from __future__ import annotations

import re

import json

from ..models import AgentInfo
from ..security import redact
from .base import LOGGED_IN, LOGGED_OUT, LOGIN_UNKNOWN, AgentAdapter, CommandSpec, Normalized, RawResult, RunContext


class ClaudeAdapter(AgentAdapter):
    name = "claude"
    display_name = "Claude Code"
    executables = ("claude",)
    install_url = "https://docs.anthropic.com/en/docs/claude-code/setup"
    status_args = ("auth", "status")
    login_args = ("auth", "login")

    def login_state(self, code, text):
        m = re.search(r'"loggedIn"\s*:\s*(true|false)', text)
        return (LOGGED_IN if m.group(1) == "true" else LOGGED_OUT) if m else LOGIN_UNKNOWN
    required_flags = ("-p", "--print", "--output-format")
    structured_flags = ("--output-format",)
    output_formats = ("json", "stream-json")
    continue_flag = "--continue"

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "-p", "--output-format")
        argv = [info.path or "claude", "-p", "--output-format", "json"]
        if ctx.autonomy == "full" and self.has(info, "--dangerously-skip-permissions"):
            argv.append("--dangerously-skip-permissions")
        elif self.has(info, "--permission-mode"):
            argv += ["--permission-mode", "acceptEdits"]
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        # Prompt goes over stdin: variadic flags like --allowedTools would otherwise swallow it.
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, stdin=ctx.prompt, env=ctx.env)

    def normalize_result(self, raw: RawResult) -> Normalized:
        text = raw.stdout.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return super().normalize_result(raw)
        if isinstance(data, list):
            data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), {})
        if not isinstance(data, dict):
            return super().normalize_result(raw)
        result = str(data.get("result") or "")
        is_error = bool(data.get("is_error"))
        return Normalized(summary=redact(result)[-3000:], session_id=data.get("session_id"),
                          is_error=is_error, error_text=result if is_error else "")
