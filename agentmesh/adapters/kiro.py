from __future__ import annotations

import json

from ..models import AgentInfo
from .base import LOGGED_IN, LOGGED_OUT, LOGIN_UNKNOWN, AgentAdapter, CommandSpec, RunContext, probe_command

# Kiro bills in credits (multiplier reported by `kiro-cli chat --list-models`). All of these answered a coding prompt in a
# live test on 2026-10-07 (~6s each, mostly startup). Ordered cheapest first, coding-specific model leading; the stronger
# but pricier claude-sonnet-4.5 (1.3x) is left out on purpose, and claude-sonnet-4 is EOL 2026-10-14.
PREFERRED_MODELS = ("qwen3-coder-next", "deepseek-3.2", "minimax-m2.5", "glm-5", "claude-haiku-4.5", "auto")


def available_models(path: str | None = None) -> list[str]:
    code, out = probe_command([path or "kiro-cli", "chat", "--list-models", "-f", "json"], timeout=45)
    if code != 0:
        return []
    try:
        return [m["model_id"] for m in json.loads(out[out.index("{"):]).get("models", [])]
    except (ValueError, KeyError, TypeError):
        return []


def available_preferred_models(path: str | None = None) -> list[str]:
    have = set(available_models(path))
    return [m for m in PREFERRED_MODELS if m in have]


class KiroAdapter(AgentAdapter):
    name = "kiro"
    display_name = "Kiro CLI"
    executables = ("kiro-cli",)
    install_url = "https://kiro.dev/docs/cli/installation/"
    install_argv = ("sh", "-c", "curl -fsSL https://cli.kiro.dev/install | bash")   # macOS/Linux, from the docs
    install_needs = "sh"
    status_args = ("whoami",)
    login_args = ("login",)

    def login_state(self, code, text):
        low = text.lower()
        if "not logged in" in low:
            return LOGGED_OUT
        return LOGGED_IN if "logged in" in low else LOGIN_UNKNOWN
    headless_help_args = ("chat", "--help")
    required_flags = ("--no-interactive", "--trust-tools")
    structured_flags = ("--output-format",)
    output_formats = ("stream-json",)
    continue_flag = "--resume"
    notes = ("edit autonomy trusts only fs_read,fs_write (tool names taken from `chat --help`); "
             "full uses --trust-all-tools",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--no-interactive")
        argv = [info.path or "kiro-cli", "chat", "--no-interactive"]
        if ctx.autonomy == "full" and self.has(info, "--trust-all-tools"):
            argv.append("--trust-all-tools")
        elif self.has(info, "--trust-tools"):
            argv.append("--trust-tools=fs_read,fs_write")
        if ctx.continue_session and self.has(info, "--resume"):
            argv.append("--resume")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv.append(ctx.prompt)
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
