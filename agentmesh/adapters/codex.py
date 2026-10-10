from __future__ import annotations

from ..models import AgentInfo
from .base import LOGGED_IN, LOGGED_OUT, LOGIN_UNKNOWN, AgentAdapter, CommandSpec, RunContext


class CodexAdapter(AgentAdapter):
    name = "codex"
    display_name = "OpenAI Codex CLI"
    executables = ("codex",)
    install_url = "https://github.com/openai/codex"
    install_argv = ("npm", "install", "-g", "@openai/codex")
    install_needs = "npm"
    status_args = ("login", "status")
    login_args = ("login",)

    def login_state(self, code, text):
        low = text.lower()
        if "not logged in" in low:
            return LOGGED_OUT
        return LOGGED_IN if "logged in" in low else LOGIN_UNKNOWN
    headless_help_args = ("exec", "--help")
    required_flags = ("--sandbox", "--cd", "--output-last-message")
    structured_flags = ("--json",)
    output_formats = ("jsonl",)
    cwd_flag = "--cd"
    # `codex exec resume --last` exists, but its option order differs; follow-ups use a fresh prompt.

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--cd", "--output-last-message")
        scratch = ctx.scratch or ctx.cwd
        out = scratch / "codex-last-message.txt"
        argv = [info.path or "codex", "exec", "--cd", str(ctx.cwd), "--output-last-message", str(out)]
        if ctx.autonomy == "full" and self.has(info, "--dangerously-bypass-approvals-and-sandbox"):
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.has(info, "--sandbox"):
            argv += ["--sandbox", "workspace-write"]
        if self.has(info, "--skip-git-repo-check"):
            argv.append("--skip-git-repo-check")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv.append(ctx.prompt)
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env, output_file=out)
