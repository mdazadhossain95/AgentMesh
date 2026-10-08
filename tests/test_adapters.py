import json
from pathlib import Path

import pytest

from conftest import make_script
from agentmesh.adapters import (AntigravityAdapter, ClaudeAdapter, CodexAdapter, FreebuffAdapter,
                                GenericAdapter, ClineAdapter, KiroAdapter, CopilotAdapter, KiloAdapter, OpenCodeAdapter)
from agentmesh.adapters.base import RawResult, RunContext, parse_flags
from agentmesh.errors import ErrorCode, UnsupportedError, classify_text
from agentmesh.models import AgentInfo, Task
from agentmesh.security import redact


def ctx(tmp_path, **kw):
    return RunContext(task=Task(task_id="t", title="t", role="r"), prompt="PROMPT", cwd=tmp_path, scratch=tmp_path, **kw)


def info(*flags, path="/bin/x"):
    return AgentInfo(name="x", display_name="X", adapter="A", installed=True, path=path, flags=list(flags))


# ---------- error normalization
@pytest.mark.parametrize("text,code", [
    ("You have exceeded your current quota", ErrorCode.QUOTA_EXCEEDED),
    ("Usage limit reached. Resets at 5pm", ErrorCode.QUOTA_EXCEEDED),
    ("insufficient_quota", ErrorCode.QUOTA_EXCEEDED),
    ("Your credit balance is too low", ErrorCode.QUOTA_EXCEEDED),
    ("HTTP 429 Too Many Requests", ErrorCode.RATE_LIMITED),
    ("rate limit exceeded, slow down", ErrorCode.RATE_LIMITED),
    ("401 Unauthorized", ErrorCode.AUTH_FAILED),
    ("Please log in to continue", ErrorCode.AUTH_FAILED),
    ("invalid api key", ErrorCode.AUTH_FAILED),
    ("503 Service Unavailable", ErrorCode.PROVIDER_UNAVAILABLE),
    ("model is overloaded", ErrorCode.PROVIDER_UNAVAILABLE),
    ("ECONNRESET", ErrorCode.PROVIDER_UNAVAILABLE),
    ("segfault in toolchain", None),
])
def test_classify_text(text, code):
    assert classify_text(text) == code


def test_adapter_classification_rules(tmp_path):
    a = ClaudeAdapter()
    n = a.normalize_result(RawResult(0, stdout="done"))
    assert a.classify_error(RawResult(None, not_found=True), n) == ErrorCode.CLI_NOT_FOUND
    assert a.classify_error(RawResult(None, timed_out=True), n) == ErrorCode.TIMEOUT
    assert a.classify_error(RawResult(1, stderr="oops"), n) == ErrorCode.WORKER_FAILED
    assert a.classify_error(RawResult(1, stderr="Error 429"), n) == ErrorCode.RATE_LIMITED
    # a successful reply that merely *talks about* quotas must not look like a failure
    ok = RawResult(0, stdout="I implemented quota handling and authentication failed paths")
    assert a.classify_error(ok, a.normalize_result(ok)) is None


def test_prose_in_stdout_head_does_not_cause_false_auth_failure():
    a = KiroAdapter()
    raw = RawResult(1, stdout="Implemented login. " + "x" * 5000 + " tests failed: expected 3 got 4", stderr="")
    assert a.classify_error(raw, a.normalize_result(raw)) == ErrorCode.WORKER_FAILED


def test_claude_json_result_is_parsed_and_errors_surface():
    a = ClaudeAdapter()
    ok = RawResult(0, stdout=json.dumps({"type": "result", "is_error": False, "result": "all good", "session_id": "s1"}))
    n = a.normalize_result(ok)
    assert n.summary == "all good" and n.session_id == "s1" and a.classify_error(ok, n) is None
    bad = RawResult(0, stdout=json.dumps({"type": "result", "is_error": True, "result": "Usage limit reached"}))
    assert a.classify_error(bad, a.normalize_result(bad)) == ErrorCode.QUOTA_EXCEEDED
    garbage = RawResult(0, stdout="not json")
    assert a.normalize_result(garbage).summary == "not json"


# ---------- command building uses only documented flags
def test_claude_command(tmp_path):
    spec = ClaudeAdapter().build_command(ctx(tmp_path), info("-p", "--output-format", "--permission-mode", "--continue"))
    assert spec.argv[1:] == ["-p", "--output-format", "json", "--permission-mode", "acceptEdits"]
    assert spec.stdin == "PROMPT"
    full = ClaudeAdapter().build_command(ctx(tmp_path, autonomy="full"), info("-p", "--output-format", "--dangerously-skip-permissions"))
    assert "--dangerously-skip-permissions" in full.argv and "acceptEdits" not in full.argv
    cont = ClaudeAdapter().build_command(ctx(tmp_path, continue_session=True), info("-p", "--output-format", "--continue"))
    assert "--continue" in cont.argv


def test_flags_not_in_help_are_never_emitted(tmp_path):
    spec = ClaudeAdapter().build_command(ctx(tmp_path), info("-p", "--output-format"))     # no --permission-mode documented
    assert "--permission-mode" not in spec.argv
    with pytest.raises(UnsupportedError):
        ClaudeAdapter().build_command(ctx(tmp_path), info("--foo"))


def test_codex_command(tmp_path):
    spec = CodexAdapter().build_command(ctx(tmp_path), info("--cd", "--output-last-message", "--sandbox", "--skip-git-repo-check"))
    a = spec.argv
    assert a[1] == "exec" and a[a.index("--sandbox") + 1] == "workspace-write" and a[-1] == "PROMPT"
    assert a[a.index("--cd") + 1] == str(tmp_path) and spec.output_file


def test_other_adapters_build(tmp_path):
    cases = [
        (AntigravityAdapter(), info("--print", "--mode", "--print-timeout"), ["--mode", "accept-edits", "--print-timeout", "1800s", "--print", "PROMPT"]),
                (KiroAdapter(), info("--no-interactive", "--trust-tools", "--trust-all-tools"),
         ["chat", "--no-interactive", "--trust-tools=fs_read,fs_write", "PROMPT"]),
        (CopilotAdapter(), info("--prompt", "--allow-tool", "--deny-tool", "--no-ask-user", "--silent"),
         ["--allow-tool=write", "--deny-tool=shell", "--no-ask-user", "--silent", "--prompt=PROMPT"]),
        (OpenCodeAdapter(), info("--dir"), ["run", "--dir", str(tmp_path), "PROMPT"]),
    ]
    for ad, inf, tail in cases:
        assert ad.build_command(ctx(tmp_path), inf).argv[1:] == tail, ad.name


def test_extra_args_and_model_pass_through(tmp_path):
    spec = KiroAdapter().build_command(ctx(tmp_path, extra_args=["--verbose"], model="m1"), info("--no-interactive", "--model"))
    assert spec.argv[1:] == ["chat", "--no-interactive", "--model", "m1", "--verbose", "PROMPT"]


def test_interactive_and_unconfigured_adapters_refuse(tmp_path):
    with pytest.raises(UnsupportedError):
        FreebuffAdapter().build_command(ctx(tmp_path), info())
    with pytest.raises(UnsupportedError):
        KiloAdapter().build_command(ctx(tmp_path), info())


def test_parse_flags():
    assert {"-p", "--print", "--output-format"} <= set(parse_flags("  -p, --print   x\n --output-format <f>"))


# ---------- generic adapter against REAL subprocesses (no network, no paid calls)
def generic(tmp_path, body, **spec):
    exe = make_script(tmp_path / "bin", "faketool", body)
    return GenericAdapter("faketool", {"executable": str(exe), "headless_args": ["--go", "{prompt}"], **spec})


def run_generic(ad, tmp_path, **kw):
    inf = ad.health_check(path_env=str(tmp_path / "bin"))
    inf.path = ad.executables[0]
    spec = ad.build_command(ctx(tmp_path, **kw), inf)
    raw = ad.execute(spec)
    norm = ad.normalize_result(raw)
    return raw, norm, ad.classify_error(raw, norm)


def test_generic_success_and_prompt_templating(tmp_path):
    ad = generic(tmp_path, 'echo "got:$2"')
    raw, norm, code = run_generic(ad, tmp_path)
    assert code is None and "got:PROMPT" in norm.summary


def test_generic_quota_failure(tmp_path):
    ad = generic(tmp_path, 'echo "Error: usage limit reached" >&2; exit 1')
    assert run_generic(ad, tmp_path)[2] == ErrorCode.QUOTA_EXCEEDED


def test_generic_timeout_kills_process_group(tmp_path):
    ad = generic(tmp_path, "sleep 30")
    inf = ad.health_check(path_env=str(tmp_path / "bin")); inf.path = ad.executables[0]
    spec = ad.build_command(ctx(tmp_path, timeout=1), inf)
    raw = ad.execute(spec)
    assert raw.timed_out and raw.duration < 15 and ad.classify_error(raw, ad.normalize_result(raw)) == ErrorCode.TIMEOUT


def test_generic_missing_executable(tmp_path):
    ad = GenericAdapter("ghost", {"executable": "definitely-not-here-xyz", "headless_args": ["{prompt}"]})
    inf = AgentInfo(name="ghost", display_name="g", adapter="G", installed=True, path="/nonexistent/ghost")
    raw = ad.execute(ad.build_command(ctx(tmp_path), inf))
    assert raw.not_found and ad.classify_error(raw, ad.normalize_result(raw)) == ErrorCode.CLI_NOT_FOUND


def test_generic_stdin_prompt_and_runs_in_cwd(tmp_path):
    exe = make_script(tmp_path / "bin", "catter", "cat; pwd")
    ad = GenericAdapter("catter", {"executable": str(exe), "stdin_prompt": True})
    raw, norm, code = run_generic(ad, tmp_path)
    assert code is None and "PROMPT" in raw.stdout and str(tmp_path.resolve()) in raw.stdout


def test_generic_without_invocation_requires_configuration(tmp_path):
    ad = GenericAdapter("noargs", {"executable": "x"})
    i = ad.health_check(path_env=str(tmp_path))
    assert not i.installed
    make_script(tmp_path / "bin", "noargs2", "echo hi")
    ad2 = GenericAdapter("noargs2", {})
    assert ad2.health_check(path_env=str(tmp_path / "bin")).state == "CONFIGURATION_REQUIRED"


def test_generic_autonomy_and_continue_args(tmp_path):
    ad = GenericAdapter("g", {"executable": "g", "headless_args": ["-p", "{prompt}"], "edit_args": ["--edit"],
                              "full_args": ["--yolo"], "continue_args": ["-c"]})
    inf = info()
    assert ad.build_command(ctx(tmp_path), inf).argv[1:] == ["--edit", "-p", "PROMPT"]
    assert ad.build_command(ctx(tmp_path, autonomy="full", continue_session=True), inf).argv[1:] == ["--yolo", "-c", "-p", "PROMPT"]


def test_cancel_stops_running_process(tmp_path):
    import threading, time
    ad = generic(tmp_path, "sleep 30")
    inf = ad.health_check(path_env=str(tmp_path / "bin")); inf.path = ad.executables[0]
    spec = ad.build_command(ctx(tmp_path, timeout=60), inf)
    out = {}
    th = threading.Thread(target=lambda: out.update(raw=ad.execute(spec, run_id="r1")))
    th.start()
    for _ in range(50):
        if "r1" in ad._procs: break
        time.sleep(0.05)
    assert ad.cancel("r1")
    th.join(10)
    assert out["raw"].exit_code not in (0, None)


# ---------- secrets
def test_redaction():
    text = ("Authorization: Bearer abcdefghijklmnop\nOPENAI_API_KEY=sk-live123456789012345\ntoken: ghp_" + "a" * 30 +
            "\nCookie: sid=abc123; other=1\n-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\nnormal text")
    r = redact(text)
    for leak in ("abcdefghijklmnop", "sk-live", "ghp_a", "sid=abc123", "MIIE"):
        assert leak not in r
    assert "normal text" in r


def test_cline_fails_closed_unless_full_autonomy(tmp_path):
    flags = info("--cwd", "--timeout", "--yolo")
    with pytest.raises(UnsupportedError):
        ClineAdapter().build_command(ctx(tmp_path), flags)
    argv = ClineAdapter().build_command(ctx(tmp_path, autonomy="full"), flags).argv
    assert argv[1:] == ["--cwd", str(tmp_path), "--timeout", "1800", "--yolo", "--", "PROMPT"]
