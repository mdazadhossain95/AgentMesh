"""`agentmesh setup`: install/login offers. Offline: fake CLIs are shell scripts, installs/logins are recorded, never run."""
from __future__ import annotations

from agentmesh import setup as setup_mod
from agentmesh.adapters.base import AgentAdapter, LOGGED_IN, LOGGED_OUT, LOGIN_UNKNOWN
from agentmesh.registry import Registry

from conftest import make_script, restricted_path


class FakeAdapter(AgentAdapter):
    name = "fakecli"
    display_name = "Fake CLI"
    executables = ("fakecli",)
    install_url = "https://example.invalid/install"
    install_argv = ("sh", "-c", "echo install")
    install_needs = "sh"
    status_args = ("status",)
    login_args = ("login",)

    def login_state(self, code, text):
        low = text.lower()
        return LOGGED_OUT if "not logged in" in low else LOGGED_IN if "logged in" in low else LOGIN_UNKNOWN

    def build_command(self, ctx, info):          # never used here
        raise NotImplementedError


class NoMetaAdapter(FakeAdapter):
    name, display_name, executables = "nometa", "No Meta", ("nometa",)
    install_url = install_argv = install_needs = status_args = login_args = None


def reg(*adapters) -> Registry:
    r = Registry()
    for a in adapters:
        r.register(a)
    return r


def session(answers):
    asked, said, ran = [], [], []
    it = iter(answers)

    def ask(q):
        asked.append(q)
        return next(it, False)
    return asked, said, ran, ask, said.append, lambda argv: (ran.append(argv) or 0)


def test_survey_reports_not_installed_and_states(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    pe = restricted_path(bindir)
    rows = setup_mod.survey(reg(FakeAdapter()), path_env=pe)
    assert [(r.name, r.installed) for r in rows] == [("fakecli", False)]
    make_script(bindir, "fakecli", 'echo "Not logged in"; exit 1')
    rows = setup_mod.survey(reg(FakeAdapter()), path_env=pe)
    assert rows[0].installed and rows[0].login == LOGGED_OUT
    make_script(bindir, "fakecli", 'echo "Logged in as someone@example.com"')
    assert setup_mod.survey(reg(FakeAdapter()), path_env=pe)[0].login == LOGGED_IN


def test_cli_without_any_info_is_not_listed_when_missing(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    assert setup_mod.survey(reg(NoMetaAdapter()), path_env=restricted_path(bindir)) == []


def test_declined_install_runs_nothing(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    asked, said, ran, ask, say, run = session([False])
    rows = setup_mod.run_setup(reg(FakeAdapter()), ask=ask, say=say, run=run, path_env=restricted_path(bindir))
    assert len(asked) == 1 and ran == []
    assert rows[0].installed is False


def test_missing_installer_tool_prints_url_and_never_asks(tmp_path):
    class NeedsNpm(FakeAdapter):
        install_argv = ("npm", "install", "-g", "x")
        install_needs = "definitely-not-a-real-tool"
    bindir = tmp_path / "bin"; bindir.mkdir()
    asked, said, ran, ask, say, run = session([True])
    setup_mod.run_setup(reg(NeedsNpm()), ask=ask, say=say, run=run, path_env=restricted_path(bindir))
    assert asked == [] and ran == []
    assert any("https://example.invalid/install" in s for s in said)


def test_logged_out_login_only_after_yes_and_output_not_echoed(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    make_script(bindir, "fakecli", 'echo "Not logged in secret@example.com"; exit 1')
    asked, said, ran, ask, say, run = session([True])
    setup_mod.run_setup(reg(FakeAdapter()), ask=ask, say=say, run=run, path_env=restricted_path(bindir))
    assert len(asked) == 1 and len(ran) == 1 and ran[0][1:] == ["login"]
    assert not any("secret@example.com" in s for s in said)       # status output is never printed

    asked, said, ran, ask, say, run = session([False])
    setup_mod.run_setup(reg(FakeAdapter()), ask=ask, say=say, run=run, path_env=restricted_path(bindir))
    assert ran == []


def test_logged_in_asks_nothing(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    make_script(bindir, "fakecli", 'echo "Logged in"')
    asked, said, ran, ask, say, run = session([])
    rows = setup_mod.run_setup(reg(FakeAdapter()), ask=ask, say=say, run=run, path_env=restricted_path(bindir))
    assert asked == [] and ran == [] and rows[0].login == LOGGED_IN


def test_only_filter(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir()
    rows = setup_mod.survey(reg(FakeAdapter()), only={"other"}, path_env=restricted_path(bindir))
    assert rows == []


def test_cli_check_without_tty_changes_nothing(tmp_path, monkeypatch, capsys):
    from agentmesh.cli import main
    bindir = tmp_path / "bin"; bindir.mkdir()
    monkeypatch.setenv("PATH", restricted_path(bindir))
    assert main(["setup"]) == 0
    assert "no changes made" in capsys.readouterr().out


def test_builtin_login_parsers():
    from agentmesh.adapters import BUILTIN_ADAPTERS
    by = {a.name: a for a in (c() for c in BUILTIN_ADAPTERS)}
    assert by["claude"].login_state(0, '{"loggedIn": true, "email": "a@b.c"}') == LOGGED_IN
    assert by["claude"].login_state(0, '{"loggedIn": false}') == LOGGED_OUT
    assert by["claude"].login_state(0, "garbage") == LOGIN_UNKNOWN
    assert by["codex"].login_state(0, "Logged in using ChatGPT") == LOGGED_IN
    assert by["codex"].login_state(1, "Not logged in") == LOGGED_OUT
    assert by["kiro"].login_state(0, "Logged in with Google") == LOGGED_IN
    assert by["opencode"].login_state(0, "1 credentials") == LOGGED_IN
    assert by["opencode"].login_state(0, "0 credentials") == LOGGED_OUT
    assert by["kilo"].login_state(0, "0 credentials") == LOGGED_OUT
    assert by["opencode"].login_state(1, "error") == LOGIN_UNKNOWN


def test_version_flag(capsys):
    import pytest
    from agentmesh import __version__
    from agentmesh.cli import main
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out
