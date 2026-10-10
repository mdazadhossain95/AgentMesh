from __future__ import annotations

from ..models import AgentInfo
from .base import AgentAdapter, CommandSpec, RunContext


class CopilotAdapter(AgentAdapter):
    name = "copilot"
    display_name = "GitHub Copilot CLI"
    executables = ("copilot",)
    install_url = "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli"
    install_argv = ("npm", "install", "-g", "@github/copilot")     # needs Node.js 22+ per the docs
    install_needs = "npm"
    login_args = ("login",)
    required_flags = ("--prompt", "--allow-tool", "--deny-tool")
    structured_flags = ("--output-format",)
    output_formats = ("json",)
    continue_flag = "--continue"
    notes = ("its help says --allow-all-tools is 'required for non-interactive mode': edit autonomy passes "
             "--allow-tool=write --deny-tool=shell instead and the CLI may still refuse; shell/test steps need autonomy=full",)

    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        self.require(info, "--prompt")
        argv = [info.path or "copilot"]
        if ctx.autonomy == "full" and self.has(info, "--allow-all-tools"):
            argv.append("--allow-all-tools")
        elif self.has(info, "--allow-tool") and self.has(info, "--deny-tool"):
            argv += ["--allow-tool=write", "--deny-tool=shell"]
        if self.has(info, "--no-ask-user"):
            argv.append("--no-ask-user")
        if self.has(info, "--silent"):
            argv.append("--silent")
        if ctx.continue_session and self.has(info, "--continue"):
            argv.append("--continue")
        if ctx.model and self.has(info, "--model"):
            argv += ["--model", ctx.model]
        argv += ctx.extra_args
        argv += [f"--prompt={ctx.prompt}"]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
