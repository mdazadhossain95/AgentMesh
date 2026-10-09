# AgentMesh

A local orchestration layer for AI coding CLIs.

You install it once. In any repository you run `agentmesh init --auto`; it analyzes the project and writes a small
`.agentmesh/` layer: **project roles** (what work exists), a **conditional workflow** (which roles run when), and
**routing + fallback policy** (which installed CLI does each role right now). Then you open whichever CLI you want
as **manager** (`claude`, `codex`, `agy`, `kiro`, ...). It reads the generated instructions, delegates roles through
`agentmesh`, and verifies the results against real git changes.

```
USER ──▶ MANAGER (the CLI you launched)
            │  reads .agentmesh/project.yaml, workflow.yaml, agents/*.md
            │  agentmesh plan / delegate / diff / verify / integrate
            ▼
         ROLE (fixed by the project)         WORKER (chosen per run)
         backend-engineer ─────router────▶   codex ──quota──▶ antigravity ──▶ kiro
```

Two layers, never mixed:

| Layer | Examples | Changes when |
|---|---|---|
| **Project agentic layer** — roles | `spec-analyst`, `backend-engineer`, `app-engineer`, `test-executor`, `reviewer` | the *project* changes |
| **Execution layer** — workers | Claude Code, Codex, Antigravity, Kiro, ... | quota, auth, outages, your preference |

A role never depends on one CLI. When a worker hits quota, a rate limit, an auth failure, a timeout, or is missing,
AgentMesh normalizes the error and re-runs the *same role, same prompt, same worktree* on the next eligible worker.

## Why use it (in plain words)

You probably have more than one AI coding CLI: some free, some paid, each with its own quota. Normally you pick one,
hit its limit, then copy your work to another by hand. AgentMesh lets all of them work together on one project.

* **Use every CLI you already have.** Claude Code, Codex, Kiro, Copilot, Kilo, OpenCode, Antigravity, Cline and others.
  Free and paid ones work side by side.
* **No quota stops.** When one CLI runs out of quota, hits a rate limit or is missing, the same task moves to the next
  CLI automatically. Nothing is lost.
* **Cheap first.** List the CLIs you know are free in `routing.free_workers` and set `routing.strategy: free-first`;
  they are tried before paid ones. AgentMesh does not guess which CLIs are free.
* **Agents for your project.** `init` reads the project and creates the right roles (backend engineer, app engineer,
  tester, reviewer, ...) as plain files in `.agentmesh/agents/`. Any CLI can play any role.
* **Safer results.** Each task runs in its own git worktree. AgentMesh checks the real git diff, runs your tests, and
  merges only verified work. Risky changes (auth, API, database) get an extra reviewer, and a different CLI than the one
  that wrote the code.
* **Fewer tokens.** `init` and `doctor` suggest token-saving tools that fit the project (RTK, caveman, CodeGraph) and
  ask you before anything is set up. The manager also gets short rules: terse replies, small diffs first, cheap checks
  always, expensive ones only when you ask.

### How to start

1. **Log in to each CLI once, yourself.** AgentMesh never logs in for you and never sees your credentials. Run
   `claude`, `codex`, `kiro-cli`, ... one time each and sign in as you normally would.
2. **Install AgentMesh once** (see [Install](#install)).
3. **In any project:** `agentmesh init --auto`, then `agentmesh doctor`. This finds your logged-in CLIs, creates the
   roles and workflow, and shows token-saving advice.
4. **Open your favorite CLI** as manager (`agentmesh launch claude`, or just `claude`) and describe the work. The
   manager plans it, hands each part to a worker, checks the result, and merges it.

If a CLI is not logged in, AgentMesh treats it as unavailable and uses the next one. Add more CLIs later and run
`agentmesh init --auto` again; your settings are kept.

## Status — read this first

This is an MVP. What is actually verified, as of the last run on the author's machine (macOS, Python 3.14):

* **Verified by automated tests (271, no network, no paid calls):** discovery, registry, config, project/Flutter detection,
  Agent Pack generation, role selection, workflow planning, routing strategies, fallback, error normalization,
  worktree isolation + integration, init idempotency, CLAUDE.md preservation, generic adapter against real subprocesses,
  CLI end to end with mock workers.
* **Verified live on real Claude Code:** the enforcement hooks (see below).
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
| OpenCode `opencode` | 1.18.16 | `run --dir` | extra; no edit-only approval flag (`autonomy: full` adds `--auto`) |
| Cline `cline` | 3.0.69 | `--cwd --timeout [--yolo]`, prompt positional | headless auto-approves all tools and has no edit-only mode, so cline is **refused unless `autonomy: full` is set explicitly** (falls back to other workers otherwise). Original install was broken (native binary SIGKILLed); `npm i -g cline` fixed it |
| Kiro `kiro-cli` | 2.24.1 | `chat --no-interactive --trust-tools=fs_read,fs_write` | `autonomy: full` → `--trust-all-tools`; output stays text |
| GitHub Copilot `copilot` | 1.0.88 | `--prompt=… --silent --no-ask-user`; edit: `--allow-tool=write --deny-tool=shell`; full: `--allow-all-tools` | help calls `--allow-all-tools` required for non-interactive mode, so edit mode may be refused until smoke-tested |
| Freebuff `freebuff` | 0.0.186 | — | **INTERACTIVE_ONLY**: no non-interactive mode in `--help`; cannot be a worker |
| Kilo `kilo` | 7.8.8 | OpenCode fork: `run --dir [--auto] [-m model]`; 10 of 12 `:free` models answered a live coding prompt | installed via `npm i -g @kilocode/cli`; ids ending `:free` are rate-limited upstream at times (classified RATE_LIMITED, next model is tried) |
| anything else | copilot seen | — | reported as "adapter required"; register via `agents.yaml` |

Auth state is always `UNKNOWN` (probing it costs a call). Remaining quota is never shown because none of these CLIs expose it.

## Install

Requires Python ≥ 3.11 and `git`. Works on macOS, Linux and Windows. No clone needed.

```bash
# 1. get uv once:  macOS/Linux: brew install uv  (or curl -LsSf https://astral.sh/uv/install.sh | sh)
#                  Windows:     winget install astral-sh.uv   (or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex")

# 2. install the global command straight from GitHub
uv tool install git+https://github.com/mdazadhossain95/AgentMesh     # or: pipx install git+https://github.com/mdazadhossain95/AgentMesh

agentmesh --help
agentmesh discover            # what is on this machine (free)
```

Update later: `uv tool upgrade agentmesh`. Try it without installing: `uvx --from git+https://github.com/mdazadhossain95/AgentMesh agentmesh discover`
(the hooks that `init` writes call plain `agentmesh`, so install it for real use).
From a clone instead: `git clone https://github.com/mdazadhossain95/AgentMesh && cd AgentMesh && uv tool install -e .`

### Platforms

| OS | Status |
|---|---|
| macOS | Tests pass in CI; also used live by the author |
| Linux | Tests pass in CI |
| Windows | Tests pass in CI (file locking, process-tree kill, `launch`, UTF-8 output are Windows-aware). **Not yet tried with real coding CLIs on Windows**: npm-installed CLIs run as `.cmd` shims, and very long multi-line prompts passed as arguments may hit Windows quoting limits. Run `agentmesh smoke <worker> --yes` first. WSL behaves like Linux and is the safest route |

CI runs the offline test suite on Ubuntu, macOS and Windows with Python 3.11 and 3.13 (`.github/workflows/ci.yml`).

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
| `plan "<request>" [--json] [--risk low\|normal\|high]` | Map a request to workflow stages/roles (+ risk tier, worker preview) |
| `delegate --role R ... [--risk T]` | Run one role as a task on a routed worker, in a git worktree, with fallback |
| `delegate --base-task T1 [--base-task T2]` | Build on earlier tasks' work (stacked) or test/review several together |
| `audit [--json]` | List direct product-code changes in the main tree (works for any manager CLI) |
| `abandon TASK` | Drop a task so it no longer blocks finishing |
| `hook pre-edit\|stop\|session-start` | Claude Code hook entry points (installed by init) |
| `run-batch FILE [--max-parallel N] [--fail-fast]` | Run a task graph: independent tasks in parallel |
| `plan "..." --emit-batch FILE` | Also write the batch file for the plan |
| `delegate --continue TASK --message "fix only X"` | Focused correction in the same worktree (max rounds from workflow) |
| `status [--json]` | Workers + tasks |
| `diff TASK [--stat]` | The task's real git diff |
| `verify TASK [--cmd ...] [--accept REASON]` | Run the project's verification commands in the task workspace |
| `integrate TASK [--cleanup] [--force]` | Merge a **verified** task branch (`--no-ff`, aborts cleanly on conflict) |
| `launch [CLI] [-- args]` | Validate config, print status, start the manager CLI |
| `configure show\|get\|set\|add-agent` | Edit human-owned config; register a generic CLI |
| `clean [--worktrees] [--state] [--all] [--task T] --yes` | Remove worktrees/runtime state (never task/report records) |
| `benchmark --quick --yes` | **Live**, cheap: 2 easy tasks on each CLI's default model. Says "it works", never ranks, never replaces a full score |
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

## Making the manager follow the loop

Instructions alone can be ignored, so `init` also installs enforcement:

* **Claude Code (real hooks, written to `.claude/settings.json`, merged with your own settings):**
  * `PreToolUse` on Edit/Write/MultiEdit/NotebookEdit: the manager is **blocked from editing product code** and told which
    `agentmesh delegate --role …` to use. Docs, `*.md`, `.agentmesh/`, `.claude/` stay editable (`enforcement.allow_paths`).
  * `Stop`: it cannot finish while recent tasks are SUCCESS-but-unverified, still running, or out of scope (verify, integrate,
    continue, or `agentmesh abandon`). It pushes back once, never in a loop.
  * `SessionStart`: injects status (roles, ready workers, unfinished tasks).
  * Workers are never affected (they carry `AGENTMESH_DEPTH ≥ 1`), and every hook **fails open** on internal errors.
  * Verified live: a Claude Code session was refused `lib/hello.dart` yet wrote `NOTES.md`, and was refused a plain "done"
    while a task was unverified.
* **Any other manager CLI:** no hook mechanism of theirs was verified, so they get the instructions plus
  `agentmesh audit` (lists uncommitted product-code changes made outside task branches; exit 1 if any).
* **Tuning:** `enforcement.mode` = `block` (default) | `warn` (allow + log to `.agentmesh/logs/enforcement.log`) | `off`;
  env `AGENTMESH_ENFORCE=off` for one session. Limits: the hook sees edit tools, not shell edits (`sed -i`, redirects); `audit` catches those afterwards.
  The hook command is `agentmesh hook …`, so `agentmesh` must be on PATH or the hook silently does nothing.

## Risk tiers and review

Every task gets a tier from its text and, later, the files it really changed (`--risk` overrides; extra globs via
`risk.high_paths` / `risk.low_paths` in project.yaml).

| Tier | Triggers | Plan | Verify | Integrate |
|---|---|---|---|---|
| low | trivial wording, or only docs/text files touched | implementer + manager check | docs/text-only diff + clean scope = verified by diff check; code changes still run the commands | verified |
| normal | everything else | spec if needed, implement, test, review | project verification commands | verified |
| high | auth, API, backend, DB/migrations, payments, contracts | adds spec and every specialist review | project verification commands | verified **and a successful reviewer task** (or `--force`) |

Text can understate risk, so `verify`/`integrate` also re-check the changed files: touching an auth/API/migration path
makes the task high even if the title said "typo". Test and review tasks inherit the tier of the work they check.

`review.mode`: `auto` (tier decides, default) | `always` | `never`. A reviewer is kept off the worker that wrote the code
(`review.require_different_worker`, default true): for normal risk it is a preference (the summary gets a NOTE if the same
worker had to review); for high risk it is a rule (no other worker available = the review task fails with NO_WORKER_AVAILABLE).

## Token saving

`init` and `doctor` report, per project, which token-saving tools fit (`economy.mode: auto|off`): RTK (shell-heavy: has
verification commands), caveman (manager sessions run long), CodeGraph (repo of 300+ files; AgentMesh never runs
`codegraph init` itself, indexing is your call), ponytail (suggest only). RTK and caveman are global tools, so AgentMesh
only checks and reports. The manager section also gets short "Token economy" rules (terse replies, diff `--stat` first,
cheap checks always, expensive ones on request). These tools' savings numbers are mostly self-reported; do not read them as billed savings.

## Benchmark: which model is actually best

`agentmesh benchmark --yes` (**live, spends quota/credits**) gives every ready CLI, and every model in the model lists
(`--wide`: every model the CLIs report), the same 7 small coding tasks in a fresh temp dir (slugify, LRU cache, interval-merge
bug fix, duration parser, expression parser, bucket bug fix, toposort). Hidden unit tests the worker never sees score the result; the harness itself is
tested (reference solutions must score 100%, stubs <100%). Results merge into `~/.agentmesh/benchmark.json`.
`init` then orders `workers.models.*` and `routing.quality_order` from these measurements (falling back to the static lists),
and `--apply` writes them into an existing project. Limits: 4 easy tasks mostly tie at 100% (speed breaks ties); it measures
reliability and speed, not hard-problem skill; scores are for one account on one day. Re-run when models or quotas change.
Last run (2026-10-07): 19 of 30 candidates scored 100% (all Kiro models except minimax-m2.5, claude, copilot, codex, cline,
7 Kilo free models); antigravity 38%; Kilo inkling-small (quota) unusable. qwen and command-code were removed (login needed / quota exhausted); restore from git history if wanted.

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
* **Limits (free and paid):** AgentMesh cannot read remaining quota (no CLI exposes it), so it reacts to the real limit message.
  If the provider prints a reset time ("try again in 2 hours", `Retry-After: 120`), that exact time becomes the cooldown for the
  worker (or just the model: `kilo::…`, tracked per model); otherwise `fallback.cooldown_seconds` applies. Work moves to the
  next model/worker immediately, and the limited one comes back by itself when its timer ends. `agentmesh status` lists what is
  cooling down and for how long. Order free models/workers first in `workers.models.*` / `routing.free_workers`.
* **Parallel runs:** `agentmesh run-batch FILE [--max-parallel N] [--fail-fast]` runs a dependency graph: tasks with no unfinished
  dependency run at the same time (default 3, `parallel.max_workers`), the rest wait. Code-writing dependencies become the
  stacked base of the next task; read-only ones (spec, analysis) are passed as written context. A failed dependency skips its
  dependents but not unrelated work. `plan "<request>" --emit-batch b.json` writes the graph for you (spec → contract →
  implementers in parallel → tests → reviews). Reviewers are steered away from the CLI that wrote the code. Safe to launch several
  `delegate` processes yourself too: task ids are reserved atomically, state files and git operations are locked.
* **Stacked work:** `delegate --base-task T1 [--base-task T2]` starts a task from the finished branches of earlier tasks (merged
  together if several), so the app role sees the backend's code and the tester/reviewer sees everything at once. Only SUCCESS tasks
  can be a base; overlapping bases fail cleanly with `GIT_CONFLICT`; the task's reported changes are still only its own;
  `integrate` of a stacked task marks the contained base tasks INTEGRATED.
* **Isolation:** every write-role task gets branch `agentmesh/<task>` in `.agentmesh-worktrees/<task>/`. Read-only roles run
  in place, or inside another task's worktree via `--reuse-worktree` (test, review). Changed files come from git; the
  worker's own list is kept as `reported_files` for comparison. Out-of-scope changes → `SCOPE_VIOLATION`, no checkpoint commit.
* **Model chains:** `workers.models.<worker>` may be a ranked list. On timeout/quota/rate-limit the worker tries its next model
  (each limited to `workers.model_timeout_seconds`, default 300) and remembers bad models for a cooldown, and only after the
  list is exhausted does fallback move to another CLI. `init` seeds `workers.models.opencode` and `workers.models.kilo` (free `:free` models) and `workers.models.kiro` (cheapest credit multipliers first) with the models your
  `opencode models` actually lists from a short ranked list (Nvidia Nemotron-3-super first, measured fastest on 2026-10-07;
  that ranking is responsiveness on one key, not a quality benchmark). Edit the list freely; it is human-owned.
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
* `benchmark` is LIVE and spends quota. opencode/kilo/cline run with approve-everything (they have no edit-only mode) inside an empty temp dir; that is not a sandbox. Hidden tests run model-written code with a minimal environment (no inherited secrets).
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

* Parallelism is thread-based inside `run-batch` and lock-based across processes; there is no cap per individual CLI beyond the router's balancing, so many parallel tasks can hit one provider's rate limit (they then fall back as usual).
* Task classification is keyword-based (editable in `workflow.yaml`), not semantic. The manager can override with `--signal`.
* Only Claude's JSON and Codex's last-message output are parsed; others are text tails.
* No cost/quota awareness beyond what a real failure tells us.
* Analyzer covers Flutter/Dart, Node, Python, Go, Rust, JVM manifests to depth 3; deeper or exotic layouts fall back to questions or `generic`.
* Windows is untested (process-group kill and shell scripts assume POSIX).

## License

MIT
