"""AgentAdapter: the only place CLI-specific knowledge lives.

An adapter answers: is my CLI here, what does its own --help say it can do, how do I
build a headless command line for a task, and how do I turn its failures into
normalized error codes. Core code never mentions a specific CLI.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ErrorCode, UnsupportedError, classify_text
from ..models import AgentInfo, Task, WorkerState, utcnow
from ..security import redact

# own process group so a timeout can end the worker and its children
_NEW_GROUP: dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                    else {"start_new_session": True})

_LONG_FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")
_SHORT_FLAG = re.compile(r"(?<![\w-])-[A-Za-z](?![\w-])")


LOGGED_IN, LOGGED_OUT, LOGIN_UNKNOWN = "logged-in", "logged-out", "unknown"


def parse_flags(help_text: str) -> list[str]:
    found = set(_LONG_FLAG.findall(help_text)) | set(_SHORT_FLAG.findall(help_text))
    return sorted(found)


@dataclass
class RunContext:
    task: Task
    prompt: str
    cwd: Path
    timeout: int = 1800
    autonomy: str = "edit"             # edit | full
    continue_session: bool = False
    model: str | None = None
    extra_args: list[str] = field(default_factory=list)
    scratch: Path | None = None        # per-run dir for output files
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class CommandSpec:
    argv: list[str]
    cwd: Path
    timeout: int
    stdin: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    output_file: Path | None = None    # CLIs that write the final message to a file


@dataclass
class RawResult:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    not_found: bool = False
    output_file_text: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration: float = 0.0


@dataclass
class Normalized:
    summary: str
    session_id: str | None = None
    is_error: bool = False
    error_text: str = ""


def probe_command(argv: list[str], timeout: int = 15, path_env: str | None = None) -> tuple[int | None, str]:
    """Run a harmless discovery command (--version/--help). Never sends a prompt."""
    env = dict(os.environ)
    if path_env is not None:
        env["PATH"] = path_env
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL, cwd=Path.home() if Path.home().exists() else None,
                           errors="replace")
        return r.returncode, (r.stdout + "\n" + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"probe failed: {e}"


class AgentAdapter(ABC):
    # ---- identity / capability metadata (override per CLI) ----
    name: str = ""
    display_name: str = ""
    executables: tuple[str, ...] = ()
    version_args: tuple[str, ...] | None = ("--version",)   # None => do not probe (a CLI whose --version has side effects)
    help_args: tuple[str, ...] = ("--help",)
    headless_help_args: tuple[str, ...] | None = None        # e.g. ("exec", "--help") for subcommand CLIs
    required_flags: tuple[str, ...] = ()                      # must appear in help to call headless "verified"
    interactive_only: bool = False
    requires_config: bool = False
    structured_flags: tuple[str, ...] = ()
    output_formats: tuple[str, ...] = ()
    continue_flag: str | None = None
    cwd_flag: str | None = None
    timeout_flag: str | None = None
    notes: tuple[str, ...] = ()
    # ---- setup metadata, used by `agentmesh setup`. Only values confirmed from the CLI's own --help or its
    # official docs; None means "unknown", never a guess. ----
    install_url: str | None = None                 # official install page, shown when no safe command is known
    install_argv: tuple[str, ...] | None = None    # run only after the user confirms
    install_needs: str | None = None               # executable the install command needs on PATH (npm, sh)
    status_args: tuple[str, ...] | None = None     # free command that reports login state (no model call)
    login_args: tuple[str, ...] | None = None      # interactive login, run in the user's terminal

    def login_state(self, code: int | None, text: str) -> str:
        """LOGGED_IN | LOGGED_OUT | UNKNOWN from the output of `status_args`."""
        return LOGIN_UNKNOWN

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen[str]] = {}

    # ---- detection / probing (no paid calls) ----
    def detect(self, path_env: str | None = None) -> Path | None:
        for exe in self.executables:
            found = shutil.which(exe, path=path_env)
            if found:
                return Path(found)
        return None

    def version(self, exe: Path, path_env: str | None = None) -> str | None:
        if self.version_args is None:
            return None
        code, out = probe_command([str(exe), *self.version_args], path_env=path_env)
        if code != 0 or not out:
            return None
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not lines:
            return None
        numbered = [ln for ln in lines if re.search(r"\d+\.\d+", ln)]
        return (numbered[0] if numbered else lines[-1])[:80]

    def health_check(self, path_env: str | None = None) -> AgentInfo:
        """Detect + parse the CLI's own help. Free: never starts a model session."""
        info = AgentInfo(name=self.name, display_name=self.display_name, adapter=type(self).__name__,
                         probed_at=utcnow())
        exe = self.detect(path_env)
        if exe is None:
            info.state = WorkerState.NOT_INSTALLED.value
            return info
        info.installed, info.path, info.executable = True, str(exe), exe.name
        info.version = self.version(exe, path_env)
        if self.version_args is None:
            info.notes.append("version not probed: this CLI's --version has side effects (auto-update)")
        help_text = self._collect_help(exe, path_env)
        info.flags = parse_flags(help_text)
        self.capabilities(info, help_text)
        return info

    def _collect_help(self, exe: Path, path_env: str | None) -> str:
        parts = [probe_command([str(exe), *self.help_args], path_env=path_env)[1]]
        if self.headless_help_args:
            parts.append(probe_command([str(exe), *self.headless_help_args], path_env=path_env)[1])
        return "\n".join(parts)

    def capabilities(self, info: AgentInfo, help_text: str) -> None:
        """Fill capability fields from facts in help output only."""
        info.notes.extend(self.notes)
        flags = set(info.flags)
        if self.interactive_only:
            info.headless, info.state = "NO", WorkerState.INTERACTIVE_ONLY.value
            info.notes.append("no non-interactive mode documented in --help")
            return
        if self.requires_config:
            info.headless, info.state = "UNKNOWN", WorkerState.CONFIGURATION_REQUIRED.value
            info.notes.append("no validated invocation; define one in ~/.agentmesh/agents.yaml")
            return
        missing = [f for f in self.required_flags if f not in flags]
        if not help_text.strip():
            info.headless, info.state = "UNKNOWN", WorkerState.UNVERIFIED.value
            info.notes.append("--help produced no output")
        elif missing:
            info.headless, info.state = "UNKNOWN", WorkerState.UNVERIFIED.value
            info.notes.append(f"expected flags not found in --help: {', '.join(missing)}")
        else:
            info.headless, info.state = "YES", WorkerState.READY.value
        if self.structured_flags:
            ok = all(f in flags for f in self.structured_flags)
            info.structured_output = "YES" if ok else "NOT_DOCUMENTED"
            info.output_formats = list(self.output_formats) if ok else []
        else:
            info.structured_output = "NOT_DOCUMENTED"
        info.session_continue = ("YES" if self.continue_flag and self.continue_flag in flags
                                 else "NOT_DOCUMENTED")
        info.cwd_behavior = f"flag {self.cwd_flag}" if self.cwd_flag and self.cwd_flag in flags else "PROCESS_CWD"
        info.native_timeout = "YES" if self.timeout_flag and self.timeout_flag in flags else "NO (AgentMesh enforces)"

    def supports_headless(self, info: AgentInfo) -> bool:
        return info.headless == "YES"

    def supports_structured_output(self, info: AgentInfo) -> bool:
        return info.structured_output == "YES"

    # ---- execution ----
    @abstractmethod
    def build_command(self, ctx: RunContext, info: AgentInfo) -> CommandSpec:
        """Return the argv for a headless run. Use only flags present in info.flags."""

    def autonomy_unavailable(self, ctx: RunContext) -> str | None:
        return None

    def execute(self, spec: CommandSpec, run_id: str = "run") -> RawResult:
        raw = RawResult(exit_code=None, started_at=utcnow())
        t0 = time.monotonic()
        env = {**os.environ, **spec.env}
        try:
            proc = subprocess.Popen(
                spec.argv, cwd=spec.cwd, env=env, text=True, errors="replace",
                stdin=subprocess.PIPE if spec.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_NEW_GROUP)
        except FileNotFoundError:
            raw.not_found = True
            return self._finish(raw, t0)
        self._procs[run_id] = proc
        try:
            raw.stdout, raw.stderr = proc.communicate(input=spec.stdin, timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            raw.stdout, raw.stderr = proc.communicate()
            raw.timed_out = True
        finally:
            self._procs.pop(run_id, None)
        raw.exit_code = proc.returncode
        if spec.output_file and spec.output_file.is_file():
            raw.output_file_text = spec.output_file.read_text(encoding="utf-8", errors="replace")
        return self._finish(raw, t0)

    @staticmethod
    def _finish(raw: RawResult, t0: float) -> RawResult:
        raw.duration = round(time.monotonic() - t0, 2)
        raw.finished_at = utcnow()
        return raw

    @staticmethod
    def _kill(proc: subprocess.Popen[str]) -> None:
        if os.name == "nt":      # no process groups: taskkill /T ends the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def cancel(self, run_id: str = "run") -> bool:
        proc = self._procs.get(run_id)
        if proc is None:
            return False
        self._kill(proc)
        return True

    # ---- result handling ----
    def normalize_result(self, raw: RawResult) -> Normalized:
        text = raw.output_file_text or raw.stdout
        return Normalized(summary=redact(text.strip())[-3000:])

    def classify_error(self, raw: RawResult, norm: Normalized) -> ErrorCode | None:
        """None => success. Only inspects failure evidence, never a successful reply's prose."""
        if raw.not_found:
            return ErrorCode.CLI_NOT_FOUND
        if raw.timed_out:
            return ErrorCode.TIMEOUT
        if raw.exit_code == 0 and not norm.is_error:
            return None
        evidence = "\n".join([raw.stderr[-8000:], raw.stdout[-800:], norm.error_text])
        return classify_text(evidence) or ErrorCode.WORKER_FAILED

    # ---- helpers for subclasses ----
    @staticmethod
    def has(info: AgentInfo, flag: str) -> bool:
        return flag in info.flags

    def require(self, info: AgentInfo, *flags: str) -> None:
        missing = [f for f in flags if f not in info.flags]
        if missing:
            raise UnsupportedError(f"{self.name}: flags not documented by this CLI's --help: {missing}")
