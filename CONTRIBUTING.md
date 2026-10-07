# Contributing

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest          # must stay green, offline, free
```

## Ground rules

1. **Tests never call a paid or networked service.** Use `MockAdapter` or shell-script fakes (`tests/conftest.py: make_script`).
2. **No invented CLI flags.** An adapter may only emit flags that appear in the CLI's own `--help`
   (`self.require`, `self.has`). Cite the version you inspected in the PR.
3. **Discovery stays free:** `detect`/`version`/`health_check` run `--version`/`--help` only; never a prompt.
   If a CLI's `--version` has side effects, set `version_args = None` (see `commandcode.py`).
4. **Agent-specific code lives in `adapters/`.** Core modules must not mention a concrete CLI.
5. **Roles never reference a CLI; workers never reference a role.** Routing is the only bridge.
6. **Generated project content derives from the analyzer.** No domain assumptions (state manager, framework, business rules).
7. Honest status: if you could not run something, label it UNVERIFIED in docs and in `AgentInfo.notes`.

## Where things go

| Change | Files |
|---|---|
| New CLI | `adapters/<name>.py`, register in `adapters/__init__.py`, tests in `tests/test_adapters.py`, row in README table |
| New stack detection | `project_analyzer.py` + `tests/test_analyzer.py` |
| New role | `roles.py` (`spec_for`, `select_roles`), `workflow.py` stage, tests in `tests/test_roles_workflow.py` |
| New error pattern | `errors.py` `_PATTERNS` + parametrized case in `tests/test_adapters.py` |

Style: typed, `pathlib`, no new dependencies without discussion (currently only PyYAML).
