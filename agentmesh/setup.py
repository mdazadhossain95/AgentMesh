"""`agentmesh setup`: walk through the coding CLIs one at a time, offer to install missing ones and to log in.

Rules (also in the README):
* nothing is installed or logged in without a yes for that one CLI;
* AgentMesh never sees credentials: the CLI's own login runs in the user's terminal;
* install commands come from adapter metadata (confirmed from official docs), shown before they run;
  where none is known, only the official page is printed;
* status output is never printed or stored, it can contain an email address: only the state is shown.

Core code stays CLI-agnostic: every CLI-specific fact lives on the adapter (install_*, status_args, login_args).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from .adapters.base import AgentAdapter, LOGGED_IN, LOGGED_OUT, LOGIN_UNKNOWN, probe_command
from .registry import Registry

Ask = Callable[[str], bool]
Say = Callable[[str], None]
Run = Callable[[list[str]], int]      # interactive command, inherits the terminal; returns the exit code


@dataclass
class SetupRow:
    name: str
    display: str
    installed: bool
    login: str = "n/a"                 # logged-in | logged-out | unknown | n/a (not installed)
    note: str = ""


def _skip(a: AgentAdapter) -> bool:
    """Not a real CLI the user can install/log in to: in-process mock, interactive-only, or no metadata at all."""
    return type(a).__name__ == "MockAdapter" or a.interactive_only


def _has_install_info(a: AgentAdapter) -> bool:
    return bool(a.install_argv or a.install_url)


def login_state(a: AgentAdapter, exe: str, path_env: str | None = None) -> str:
    if not a.status_args:
        return LOGIN_UNKNOWN
    code, text = probe_command([exe, *a.status_args], path_env=path_env)
    if code is None:
        return LOGIN_UNKNOWN
    return a.login_state(code, text)


def survey(registry: Registry, *, only: set[str] | None = None, path_env: str | None = None) -> list[SetupRow]:
    """Read-only: what is installed and what the free status commands report. Runs no install or login."""
    rows: list[SetupRow] = []
    for a in registry.adapters():
        if _skip(a) or (only and a.name not in only):
            continue
        exe = a.detect(path_env)
        if exe is None:
            if _has_install_info(a):
                rows.append(SetupRow(a.name, a.display_name or a.name, False))
            continue
        rows.append(SetupRow(a.name, a.display_name or a.name, True, login_state(a, str(exe), path_env)))
    return rows


def _install(a: AgentAdapter, ask: Ask, say: Say, run: Run) -> bool:
    """Offer to install. True when the command ran successfully (caller re-detects the executable)."""
    if a.install_argv and shutil.which(a.install_needs or a.install_argv[0]):
        say(f"  install command: {' '.join(a.install_argv)}")
        if a.install_url:
            say(f"  docs: {a.install_url}")
        if not ask(f"  Install {a.display_name or a.name} now?"):
            say("  skipped")
            return False
        code = run(list(a.install_argv))
        if code != 0:
            say(f"  install exited with code {code}")
        return code == 0
    if a.install_argv:
        say(f"  needs `{a.install_needs or a.install_argv[0]}`, which is not on PATH.")
    if a.install_url:
        say(f"  install it yourself: {a.install_url}")
    else:
        say("  no install information known for this CLI.")
    return False


def _login(a: AgentAdapter, exe: str, state: str, ask: Ask, say: Say, run: Run, path_env: str | None) -> str:
    if not a.login_args:
        say(f"  log in by starting `{a.executables[0]}` once and following its prompts.")
        return state
    question = (f"  Not logged in. Log in to {a.display_name or a.name} now?" if state == LOGGED_OUT
                else f"  Login state unknown. Run `{a.executables[0]} {' '.join(a.login_args)}` anyway?")
    if not ask(question):
        say("  skipped")
        return state
    say(f"  running: {a.executables[0]} {' '.join(a.login_args)}   (finish in the browser if it opens one)")
    code = run([exe, *a.login_args])
    if code != 0:
        say(f"  login exited with code {code}")
    return login_state(a, exe, path_env) if a.status_args else LOGIN_UNKNOWN


def run_setup(registry: Registry, *, ask: Ask, say: Say, run: Run, only: set[str] | None = None,
              path_env: str | None = None) -> list[SetupRow]:
    """Interactive pass. Returns the final state of each CLI."""
    final: list[SetupRow] = []
    for a in registry.adapters():
        if _skip(a) or (only and a.name not in only):
            continue
        exe = a.detect(path_env)
        label = a.display_name or a.name
        if exe is None:
            if not _has_install_info(a):
                continue
            say(f"{label}: not installed")
            if _install(a, ask, say, run):
                exe = a.detect(path_env)
                if exe is None:
                    say("  installed, but not on PATH yet: open a new terminal, then run `agentmesh setup` again.")
            if exe is None:
                final.append(SetupRow(a.name, label, False))
                continue
        state = login_state(a, str(exe), path_env)
        if state == LOGGED_IN:
            say(f"{label}: installed, logged in")
        else:
            say(f"{label}: installed, {'not logged in' if state == LOGGED_OUT else 'login state unknown'}")
            state = _login(a, str(exe), state, ask, say, run, path_env)
            if state == LOGGED_IN:
                say(f"  {label}: logged in")
        final.append(SetupRow(a.name, label, True, state))
    return final


def interactive_run(argv: list[str]) -> int:
    """Run in the user's terminal with stdio attached (installers print progress, logins open a browser)."""
    try:
        return subprocess.run(argv).returncode
    except OSError as e:
        print(f"  could not run {argv[0]}: {e}")
        return 127
