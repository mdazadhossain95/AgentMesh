from pathlib import Path

from conftest import make_script
from agentmesh import discovery
from agentmesh.registry import build_registry

HELP_CLAUDE = 'echo "  -p, --print   Print response"; echo "  --output-format <f>"; echo "  --permission-mode <m>"; echo "  --continue"'


def claude_script(bindir):
    return make_script(bindir, "claude", f'case "$1" in --version) echo "9.9.9 (Claude Code)";; --help) {HELP_CLAUDE};; esac')


def run(tmp_path, **kw):
    return discovery.discover(build_registry(custom={}, mock_env=False), path_env=f"{tmp_path/'bin'}:/usr/bin:/bin", **kw)


def test_nothing_installed_marks_everything_not_installed(tmp_path):
    rep = discovery.discover(build_registry(custom={}, mock_env=False), path_env=str(tmp_path / "empty"))
    assert rep.agents and all(not a.installed and a.state == "NOT_INSTALLED" for a in rep.agents)


def test_detects_claude_from_its_own_help(tmp_path):
    claude_script(tmp_path / "bin")
    a = run(tmp_path).by_name()["claude"]
    assert a.installed and a.version == "9.9.9 (Claude Code)"
    assert a.state == "READY" and a.headless == "YES" and a.structured_output == "YES"
    assert a.session_continue == "YES" and a.auth == "UNKNOWN" and a.smoke == "NOT_RUN"


def test_missing_expected_flags_is_unverified_not_ready(tmp_path):
    make_script(tmp_path / "bin", "claude", 'case "$1" in --version) echo 1.0;; --help) echo "  --foo";; esac')
    a = run(tmp_path).by_name()["claude"]
    assert a.state == "UNVERIFIED" and a.headless == "UNKNOWN"
    assert any("expected flags" in n for n in a.notes)


def test_interactive_only_cli(tmp_path):
    make_script(tmp_path / "bin", "freebuff", 'case "$1" in --version) echo 0.1;; --help) echo "  --cwd <d>";; esac')
    a = run(tmp_path).by_name()["freebuff"]
    assert a.state == "INTERACTIVE_ONLY" and a.headless == "NO" and not a.ready


def test_kilo_is_ready_only_when_run_help_documents_dir(tmp_path):
    make_script(tmp_path / "bin", "kilo", 'case "$1" in --version) echo 7.8.8;; run) echo "  --dir  directory"; echo "  --model";; *) echo "  --help";; esac')
    a = run(tmp_path).by_name()["kilo"]
    assert a.installed and a.state == "READY" and a.version == "7.8.8"
    make_script(tmp_path / "bin", "kilo", 'echo "  --help"')
    assert run(tmp_path).by_name()["kilo"].state == "UNVERIFIED"


def test_version_is_never_executed_when_the_adapter_opts_out(tmp_path):
    from agentmesh.adapters import ClaudeAdapter
    from agentmesh.registry import Registry

    class NoVersion(ClaudeAdapter):
        name, executables, version_args = "nv", ("nv",), None

    marker = tmp_path / "ran-version"
    make_script(tmp_path / "bin", "nv",
                f'case "$1" in --version) touch {marker}; echo 1;; --help) echo "  -p, --print"; echo "  --output-format";; esac')
    reg = Registry()
    reg.register(NoVersion())
    a = discovery.discover(reg, path_env=f"{tmp_path/'bin'}:/usr/bin:/bin").by_name()["nv"]
    assert a.state == "READY" and a.version is None and not marker.exists()


def test_unknown_candidate_clis_are_reported_but_not_adapted(tmp_path):
    make_script(tmp_path / "bin", "aider", "echo aider")
    make_script(tmp_path / "bin", "foo-code", "echo foo")
    make_script(tmp_path / "bin", "gpg-agent", "echo not-an-ai")
    names = {u.name for u in run(tmp_path).unknown}
    assert names == {"aider", "foo-code"}


def test_cache_roundtrip(tmp_path, home):
    claude_script(tmp_path / "bin")
    rep = run(tmp_path)
    discovery.save_cache(rep)
    again = discovery.load_cache()
    assert again.by_name()["claude"].version == "9.9.9 (Claude Code)"
