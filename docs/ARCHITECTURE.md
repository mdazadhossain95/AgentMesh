# Architecture (for contributors)

AgentMesh separates **roles** (fixed per project) from **workers** (AI CLIs chosen per run).
A router bridges the two; roles never name a CLI and workers never name a role.

```
cli.py ──▶ task_manager ──▶ router ──▶ runner ──▶ adapters/<cli>.py ──▶ the CLI
              │                │          │
           roles/workflow   registry   worktree + scope + git diff
```

## Modules

| Area | Files |
|---|---|
| Entry point | `cli.py`, `__main__.py` |
| Find CLIs | `discovery.py`, `registry.py`, `health.py`, `setup.py` |
| Adapters (one per CLI) | `adapters/base.py`, `adapters/<cli>.py`, `adapters/generic.py`, `adapters/mock.py` |
| Understand the repo | `project_analyzer.py`, `roles.py`, `workflow.py`, `initializer.py`, `bootstrap.py`, `templates/` |
| Choose a worker | `router.py`, `economy.py`, `risk.py`, `benchmark.py` |
| Run a task | `runner.py`, `task_manager.py`, `delegation.py`, `batch.py`, `worktree.py`, `scope.py`, `gitutil.py`, `progress.py` |
| Safety | `security.py` (secret redaction), `enforcement.py` (Claude hooks), `locking.py`, `fsutil.py` |
| State and errors | `state.py`, `models.py`, `errors.py`, `config.py` |

## Rules that hold the design together

1. Changed files always come from `git`, never from what a worker says.
2. Scope globs are enforced after each run.
3. Fallback to the next worker happens only for infrastructure errors (quota, timeout), not for bad work.
4. AgentMesh runs verification itself; `integrate` needs a verified task.
5. Discovery is free: `--version` and `--help` only, never a prompt.
6. Generated files carry a content hash; files a human edited are kept, not overwritten.

## Add a new AI CLI (the easiest contribution)

1. Run `<cli> --help` and read how it runs non-interactively.
2. Copy a small adapter, for example `adapters/kilo.py`, to `adapters/<name>.py`.
3. Emit only flags that appear in that `--help` (`self.require`, `self.has`).
4. Register it in `adapters/__init__.py`.
5. Add tests in `tests/test_adapters.py` using a script fake (`make_script`), never the real CLI.
6. Add a row to the README/REFERENCE table. Mark anything you could not run as UNVERIFIED.

If the CLI is only a thin wrapper, you may not need Python at all:
`agentmesh configure add-agent` registers a generic adapter.

## Other good first contributions

* A new stack in `project_analyzer.py` (plus a test in `tests/test_analyzer.py`).
* A new error pattern in `errors.py` (`_PATTERNS`) with a parametrized test.
* Run the smoke test on Windows or Linux and report the result.
* Improve a role template in `templates/`.
