# AgentMesh

A local orchestration layer for AI coding CLIs.

You install it once. In any repository you run `agentmesh init --auto`; it analyzes the project and writes a small
`.agentmesh/` layer: **project roles** (what work exists), a **conditional workflow** (which roles run when), and
**routing + fallback policy** (which installed CLI does each role right now). Then you open whichever CLI you want
as **manager** (`claude`, `codex`, `agy`, `qwen`, ...). It reads the generated instructions, delegates roles through
`agentmesh`, and verifies the results against real git changes.

```
USER ──▶ MANAGER (the CLI you launched)
            │  reads .agentmesh/project.yaml, workflow.yaml, agents/*.md
            │  agentmesh plan / delegate / diff / verify / integrate
            ▼
         ROLE (fixed by the project)         WORKER (chosen per run)
         backend-engineer ─────router────▶   codex ──quota──▶ antigravity ──▶ qwen
```

Two layers, never mixed:

| Layer | Examples | Changes when |
|---|---|---|
| **Project agentic layer** — roles | `spec-analyst`, `backend-engineer`, `app-engineer`, `test-executor`, `reviewer` | the *project* changes |
| **Execution layer** — workers | Claude Code, Codex, Antigravity, Qwen, ... | quota, auth, outages, your preference |

A role never depends on one CLI. When a worker hits quota, a rate limit, an auth failure, a timeout, or is missing,
AgentMesh normalizes the error and re-runs the *same role, same prompt, same worktree* on the next eligible worker.

## Status — read this first

This is an MVP. What is actually verified, as of the last run on the author's machine (macOS, Python 3.14):

* **Verified by automated tests (160, no network, no paid calls):** discovery, registry, config, project/Flutter detection,
  Agent Pack generation, role selection, workflow planning, routing strategies, fallback, error normalization,
  worktree isolation + integration, init idempotency, CLAUDE.md preservation, generic adapter against real subprocesses,
  CLI end to end with mock workers.
* **Verified against the real CLIs on that machine (free `--version` / `--help` only):** that each adapter's required
  flags exist in the installed CLI's own help.
* **NOT verified: any live model call.** No prompt was ever sent to a real CLI during development (cost, and
  because discovery must be free). So every integration is "flags confirmed in `--help`, command shape unit-tested",
  not "observed working". `agentmesh smoke <worker> --yes` performs one tiny live check when *you* choose to spend it.
  Output parsing is real only for Claude's `--output-format json` (unit-tested on its documented shape) and Codex's
  last-message file; other CLIs' output is kept as text.

| CLI | Found here | Adapter basis | Notes |
|---|---|---|---|
| Claude Code `claude` | 2.1.292 | `-p --output-format json`, `--permission-mode acceptEdits`, prompt on stdin | JSON result parsed |
| Codex `codex` | 0.160.0 | `exec --cd --sandbox workspace-write --output-last-message` | follow-ups use a fresh prompt (no `resume`) |
| Antigravity `agy` | 1.3.0 | `--print <prompt> --mode accept-edits --print-timeout` | prompt-as-value form is *inferred* from help; smoke-test it |
| Qwen Code `qwen` | 0.21.8 | `--prompt` | help shows no approval flag: headless edit approval depends on your Qwen settings; add one via `workers.extra_args.qwen` |
| Gemini CLI `gemini` | 0.47.0 | `--prompt --approval-mode auto_edit` | extra, found on PATH |
| OpenCode `opencode` | 1.18.16 | `run --dir` | extra; no edit-only approval flag (`autonomy: full` adds `--auto`) |
| Command Code `command-code` | 1.65.0 | `--print --permission-mode accept-edits` | version never probed: its `--version` **self-updates the CLI** |
| Freebuff `freebuff` | 0.0.186 | — | **INTERACTIVE_ONLY**: no non-interactive mode in `--help`; cannot be a worker |
| Kilo `kilo` | not installed | placeholder | **CONFIGURATION_REQUIRED**: define its invocation in `agents.yaml` |
| anything else | cline, copilot, kiro-cli seen | — | reported as "adapter required"; register via `agents.yaml` |

Auth state is always `UNKNOWN` (probing it costs a call). Remaining quota is never shown because none of these CLIs expose it.

## Install

Requires Python ≥ 3.11 and `git`.

```bash
git clone <this repo> ~/AgentMesh && cd ~/AgentMesh

# global command, isolated env (recommended)
uv tool install -e .          # or: pipx install -e .   /   pipx install .
# or into the current environment
pip install -e .

agentmesh --help
agentmesh discover            # what is on this machine (free)
```

Dev: `uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest`.

## Use

```bash
cd ~/Projects/GoVio
agentmesh init --auto         # analyze → ask only what files can't answer → generate .agentmesh/
agentmesh doctor              # free health check
agentmesh launch claude       # validate, show worker status, start the manager (or just: claude)
```

Then tell the manager: *"Implement the profile API and Flutter profile screen."* It already knows the workflow
(from the managed section in `CLAUDE.md` / `AGENTS.md`), runs `agentmesh plan`, delegates each stage, inspects the git
diff, runs `agentmesh verify`, sends focused corrections with `delegate --continue`, and only then `integrate`s.

### Commands

| Command | What it does |
|---|---|
| `discover [--json]` | Probe this machine for coding CLIs (`--version`/`--help` only) and unknown candidates |
| `agents [--refresh]` | Known workers, cached state, runtime status (cooldown/failures) |
| `analyze [--json]` | Show what init would detect; writes nothing |
| `init [--auto] [--yes] [--manager X] [--reference PATH\|URL] [--answer id=val] [--security\|--no-security] [--dry-run]` | Generate/refresh `.agentmesh/` and manager bootstrap |
| `doctor [--json]` | Config, roles, workflow, workers, routing depth, git, hygiene. No model calls |
| `plan "<request>" [--json]` | Map a request to workflow stages/roles (+ worker preview) |
| `delegate --role R ...` | Run one role as a task on a routed worker, in a git worktree, with fallback |
| `delegate --continue TASK --message "fix only X"` | Focused correction in the same worktree (max rounds from workflow) |
| `status [--json]` | Workers + tasks |
| `diff TASK [--stat]` | The task's real git diff |
| `verify TASK [--cmd ...] [--accept REASON]` | Run the project's verification commands in the task workspace |
| `integrate TASK [--cleanup] [--force]` | Merge a **verified** task branch (`--no-ff`, aborts cleanly on conflict) |
| `launch [CLI] [-- args]` | Validate config, print status, start the manager CLI |
| `configure show\|get\|set\|add-agent` | Edit human-owned config; register a generic CLI |
| `clean [--worktrees] [--state] [--all] [--task T] --yes` | Remove worktrees/runtime state (never task/report records) |
| `smoke WORKER --yes` | **Live**, may cost quota: one tiny prompt to confirm the invocation works |

Exit codes: `0` ok · `1` error · `3` task FAILED · `4` task SCOPE_VIOLATION.

## What init generates

```
GoVio/
├── CLAUDE.md                 # your text + one AgentMesh-managed block (AGENTS.md likewise)
├── .gitignore                # + AgentMesh block (runtime/logs/state/worktrees)
└── .agentmesh/
    ├── project.yaml          # detected facts + routing/fallback/delegation policy
    ├── workflow.yaml         # conditional stages + signal vocabulary
    ├── agents/*.md           # one file per relevant role (not all roles, only relevant ones)
    ├── references/index.md   # only with --reference
    ├── tasks/  reports/      # task + result JSON (+ history, verify results)
    └── runtime/ logs/ state/ # scratch, redacted worker logs, cooldowns (gitignored)
```

See `examples/flutter-backend/` for a real generated pack.

**Roles chosen from the repo, not from a template:**
Flutter only → `project-analyst spec-analyst app-engineer test-executor failure-triage reviewer`;
Flutter + backend adds `contract-keeper backend-engineer`; backend only drops `app-engineer`; web adds `frontend-engineer`;
no recognised stack → `software-engineer`. `security-reviewer` appears only when auth/crypto/secret-storage signals exist
(≥ 2) or money/KYC signals exist; `compliance-reviewer` and `threat-reviewer` need stronger financial/compliance signals.

**Role file sections:** Role · Purpose · When This Role Is Used · Read Before Work · Responsibilities · Allowed Changes ·
Must Not Change · Project Rules · Required Verification · Expected Output · Report Back Format · Handoff Contract.
Project Rules come from the analyzer (for Flutter: the *detected* state manager, router, l10n, codegen, platform dirs —
nothing is assumed; with no state package found it says so). Allowed/forbidden path globs are copied into `project.yaml`
and **enforced** after every run from `git`.

**Questions** are asked only when files can't settle them: an unrecognised `backend/` directory, a repo with no manifest,
multiple backends, a lone security-ish module. Non-interactive (`--yes`, no TTY) uses defaults and records them under
`project.assumptions`.

**Idempotency:** re-running `init` never duplicates the CLAUDE.md block, never overwrites a role/workflow file you edited
(generated files carry a content hash; edited ones are `kept`), preserves all human-owned `project.yaml` sections
(`manager routing fallback delegation workers worktree references overrides`), and reports roles that are no longer
selected as stale instead of deleting them. Put per-project tweaks under `overrides.roles.<role>`.

### Commit policy

| Commit | Ignore |
|---|---|
| `.agentmesh/project.yaml`, `workflow.yaml`, `agents/`, `references/index.md`, `README.md` | `.agentmesh/runtime/` `logs/` `state/` |
| the managed block in `CLAUDE.md` / `AGENTS.md` | `.agentmesh-worktrees/` |
| `tasks/`, `reports/` if you want an audit trail | anything secret (none is ever written) |

## Workflow

`workflow.yaml` is data: stages, the role each runs, and the signals that switch them on. `agentmesh plan` evaluates it.

```
spec (if feature / multi-area / ambiguous, skipped for trivial) → contract (if API) → implement-* (by area)
→ test (after any implementation) → [triage only if test/review fails] → review → security/compliance/threat (by signal)
→ manager-verify
```

"Fix padding on the profile screen" → `app-engineer → test-executor → reviewer`.
"Add a booking endpoint" → `spec-analyst → contract-keeper → backend-engineer → test-executor → reviewer`.
If the area can't be inferred and several engineers exist, `plan` says `needs_clarification` rather than guessing.

## Routing, fallback, isolation

* **Strategies** (`routing.strategy`): `balanced` (least-used first), `quality-first` (your `routing.quality_order`),
  `free-first` (your `routing.free_workers` first), `preferred-order`. Per-role lists in `routing.roles.<role>`.
  AgentMesh holds no cost or quality data; those lists are *your* preferences.
* **Eligibility:** installed, `READY`, not disabled, not in cooldown. `UNVERIFIED`/`INTERACTIVE_ONLY` workers are skipped
  unless `routing.allow_unverified`.
* **Fallback:** on `QUOTA_EXCEEDED RATE_LIMITED AUTH_FAILED CLI_NOT_FOUND TIMEOUT PROVIDER_UNAVAILABLE UNSUPPORTED` the next
  worker continues in the same worktree with a handover note (max `fallback.max_attempts`, default 3; cooldowns per code).
  `WORKER_FAILED`/unknown failures do **not** fall back: the work itself failed, so the manager decides.
  Classification looks at stderr and the tail of stdout only on failure, so a successful reply that merely mentions "quota"
  is never mistaken for one.
* **Isolation:** every write-role task gets branch `agentmesh/<task>` in `.agentmesh-worktrees/<task>/`. Read-only roles run
  in place, or inside another task's worktree via `--reuse-worktree` (test, review). Changed files come from git; the
  worker's own list is kept as `reported_files` for comparison. Out-of-scope changes → `SCOPE_VIOLATION`, no checkpoint commit.
* **Depth:** `AGENTMESH_DEPTH` is passed to workers; `delegation.max_depth` (default 1) blocks recursive delegation.
* **Autonomy:** `workers.autonomy: edit` (default; each CLI's edit-only/accept-edits mode where it has one) or `full`
  (the CLI's permission-bypass flag; `doctor` warns). Override per role with `workers.role_autonomy`. In `edit` mode a CLI
  may refuse to run shell commands headlessly; workers are told to report `blocked` rather than guess, and the manager
  runs `agentmesh verify` itself.

## Adapter development

Agent-specific logic lives only in `agentmesh/adapters/`. To add a CLI:

1. **No code:** `agentmesh configure add-agent mytool --executable mytool --headless-arg=--print --headless-arg='{prompt}'`
   (or edit `~/.agentmesh/agents.yaml`, see `examples/agents.yaml`). Tokens: `{prompt} {cwd} {model} {timeout}`; optional
   `edit_args`, `full_args`, `continue_args`, `stdin_prompt`.
2. **Code:** subclass `AgentAdapter`, set metadata, implement `build_command`:

```python
class MyAdapter(AgentAdapter):
    name = "mytool"; display_name = "My Tool"; executables = ("mytool",)
    required_flags = ("--print",)           # must appear in `mytool --help`, else the worker is UNVERIFIED
    structured_flags = ("--json",); output_formats = ("json",)
    continue_flag = "--continue"

    def build_command(self, ctx, info):
        self.require(info, "--print")       # only emit flags the CLI's help documents
        argv = [info.path, "--print", ctx.prompt]
        return CommandSpec(argv=argv, cwd=ctx.cwd, timeout=ctx.timeout, env=ctx.env)
```

   Register it in `agentmesh/adapters/__init__.py` (`BUILTIN_ADAPTERS`). Override `normalize_result` /
   `classify_error` only if the CLI has structured output or special failure text. Rules: never invent flags, never send a
   prompt from `detect`/`health_check`, set `version_args = None` if `--version` has side effects. Test with
   `MockAdapter` or a shell-script fake (see `tests/test_adapters.py`).

## Security notes

* No credentials are read, stored, or required. Workers use their own CLI logins; project files contain none (`doctor` scans for secret-like strings).
* Everything persisted from worker output (`reports/`, `logs/`) and everything `verify` prints passes through a redactor
  (bearer/basic tokens, cookies, `*_KEY/SECRET/TOKEN/PASSWORD=`, `sk-…`, `ghp_…`, `AKIA…`, JWTs, private-key blocks). Redaction is
  pattern-based: treat logs as sensitive anyway.
* Role policies forbid `.env*`, key/cert files, `secrets/`, `.git/`, `.agentmesh/` for workers; violations are flagged from git.
* `autonomy: full` removes the CLI's own guardrails. The worktree protects your branch, not your machine.
* Workers run as you. The worktree separates changes; it is not a sandbox.
* Nothing is pushed anywhere. `integrate` merges locally, only verified branches (or `--force`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `NO_WORKER_AVAILABLE` | `agentmesh agents`, `agentmesh doctor`: nothing READY, all in cooldown, or all disabled. `agentmesh clean --state --yes` clears cooldowns |
| Worker shows `UNVERIFIED` | its `--help` lacks a flag the adapter needs (CLI changed). `agentmesh discover` lists which; set `routing.allow_unverified: true` at your own risk |
| `worktree isolation needs at least one commit` | commit once in the repo |
| `main working tree has uncommitted changes` on integrate | commit/stash first (`.agentmesh/` changes are ignored) |
| Every task is `SCOPE_VIOLATION` | role globs too narrow: `agentmesh configure set overrides.roles.<role>.allowed_paths '["lib/**"]'` or `delegate --allow GLOB` |
| Worker can't run tests (permission denied) | `workers.role_autonomy.test-executor: full`, or rely on `agentmesh verify` |
| `agentmesh: command not found` | `uv tool update-shell` / ensure `~/.local/bin` is on PATH |
| Stale worker list | `agentmesh discover` (rewrites `~/.agentmesh/discovery.json`) |
| Env | `AGENTMESH_HOME` moves the global dir; `AGENTMESH_MOCK="a:QUOTA_EXCEEDED,b:SUCCESS"` registers fake workers for demos/tests |

## Known limitations

* Sequential execution. Worktrees and task records are parallel-safe by design (one dir/branch per task) but the runner is not concurrent yet.
* Task classification is keyword-based (editable in `workflow.yaml`), not semantic. The manager can override with `--signal`.
* Only Claude's JSON and Codex's last-message output are parsed; others are text tails.
* No cost/quota awareness beyond what a real failure tells us.
* Analyzer covers Flutter/Dart, Node, Python, Go, Rust, JVM manifests to depth 3; deeper or exotic layouts fall back to questions or `generic`.
* Windows is untested (process-group kill and shell scripts assume POSIX).

## License

MIT
