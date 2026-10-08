# AgentMesh — project summary

Local CLI (`agentmesh`, Python ≥3.11, only dep PyYAML) that sits above AI coding CLIs. Project **roles** (fixed per repo)
are separated from **workers** (CLIs chosen per run by a router with fallback).

## 1. Layout
`agentmesh/` cli · config · models · errors · security(redact) · discovery · registry · router · runner · prompting · scope ·
worktree · progress · gitutil · task_manager · state · project_analyzer · roles · workflow · initializer · bootstrap · reference · health · fsutil ·
`adapters/` base, claude, codex, antigravity, opencode, cline, kiro, copilot, freebuff, kilo, generic, mock · `templates/` ·
`tests/` (224) · `examples/`.

## 2. Key decisions
* Adapters emit only flags found in the CLI's own `--help`; state READY/UNVERIFIED/INTERACTIVE_ONLY/CONFIGURATION_REQUIRED.
* Adapters can set `version_args=None` for CLIs whose `--version` has side effects (command-code did; it was removed).
* Generated files carry a content hash: human-edited files are `kept`; project.yaml splits machine vs human keys.
* Changed files always from git; scope globs enforced after each run; fallback only for infra errors.
* Verification is run by AgentMesh (`verify`), `integrate` requires verified.

## 3. Implementation status
Done: discovery, registry, generic+mock adapters, analyzer (Flutter/Node/Python/Go/Rust/JVM), role pack + workflow generation,
init (idempotent), manager bootstrap (CLAUDE.md/AGENTS.md), router (4 strategies), runner with fallback,
worktrees, plan/delegate/continue/diff/verify/integrate/status/clean/configure/launch/doctor/smoke, README/CONTRIBUTING/LICENSE.
Not done / unverified: no live model call ever made (use `agentmesh smoke <w> --yes`); antigravity prompt-as-value form inferred;
Windows untested; keyword-based task classification.
Installed globally on dev machine with `uv tool install -e .`.

Added after MVP: model chains per worker (opencode/kilo/kiro), limit-aware cooldowns (parses reset time), stacked tasks (--base-task), parallel run-batch, enforcement (Claude hooks pre-edit/stop/session-start, audit, abandon). Fixed scope.py dotfile glob bug (.env never matched). Workers: claude codex antigravity qwen opencode command-code cline kiro copilot kilo (freebuff interactive-only; gemini removed).

Live progress (progress.py): while a worker runs, stderr shows `[task] worker/model started (timeout, typical ~Ns from benchmark)`, then a line every `progress.interval_seconds` (default 15) with elapsed, ~left, timeout; plus finished time. ETA is rough (benchmark tasks are small).

Benchmark (agentmesh benchmark, live): results in ~/.agentmesh/benchmark.json; init uses them for model chains + quality_order. Antigravity: command form is fine (edits work); the CLI default model scored 38-62%, gemini-3.1-pro-high 75%, gemini-3.8-flash-high 0%, claude-sonnet-4-6 QUOTA_EXCEEDED -> init now seeds workers.models.antigravity=[gemini-3.1-pro-high] (list via `agy models`). Opencode benchmark done. Progress ETA is a small-task baseline, not a real estimate. Open items: tasks too easy to separate the top group; qwen and command-code removed on request (restore from git history).

## 4. Next ideas
Live smoke per worker + store results; parse more structured outputs; parallel runner; semantic task classification; `agentmesh adopt` for existing agent packs.

## 5. Benchmark state (2026-10-08)
7 tasks now (added expr, bucket, toposort; reference solutions in tests/bench_refs.py). 7-task run: claude, copilot, codex, cline, kiro (deepseek-3.2, claude-sonnet-4.5, auto, claude-haiku-4.5) all 100%; kiro minimax/glm/qwen 99% (expr 0.9); antigravity 71% (gemini-3.7-flash-high, 3.1-pro-high; slugify 0). Top group still not separated -> use speed as tiebreak. Kilo/opencode entries in benchmark.json are older 4-task scores (not comparable); re-run to refresh. Antigravity chain seeded: gemini-3.7-flash-high, gemini-3.1-pro-high.

Benchmark staleness: saved rows now carry cli_version + at; `benchmark.stale_messages` warns (doctor 'Benchmark' section, init output) when scores are >30 days old, CLI version changed, fewer tasks than current, or missing. Currently flags antigravity (old default-model row), kilo, opencode (old 4-task runs) until re-run.

## 6. Todo (saved 2026-10-09; nothing below started)
Main, in build order:
1. Manager setup for any CLI. `bootstrap.MANAGER_FILES` writes only CLAUDE.md (claude) and AGENTS.md (codex, opencode). Verify what agy, kiro, copilot, cline, kilo read (do not guess), have `init` write it from the same template, add a `doctor` line per manager. Only Claude has hard enforcement hooks; other managers rely on instructions + `agentmesh audit` + integrate-needs-verified.
2. Risk tiers (low/normal/high) from touched files (scope globs) + task text, `--risk` override. High (backend/API/auth/DB): full plan, tests, verify, reviewer. Normal: verify. Low (typo/docs/config): quick diff check only. Build together with 3.
3. Token-saving setup decided per project by `init` (`tokens: auto|off` in project.yaml, `doctor` shows reasons). RTK: shell-heavy work. caveman: long sessions. codegraph: big repo (~300+ files), ask before `codegraph init`. ponytail: new-feature code, suggest only. RTK/caveman are global, so init only checks and reports; add concise-reply rules to the manager section.
4. Second-agent review driven by risk tier: reviewer must be a different worker than the author (`--avoid-worker` exists; check the router enforces it). `review: auto|always|never`.
5. `agentmesh benchmark --quick` (2 tasks, default model only).
Optional: re-run kilo/opencode benchmarks (stale warnings); cost-aware routing; fallback on failed verify; smarter task classification; `agentmesh adopt`; CI + PyPI; run-history report.
Open question: Minimi leftover files in ~/Library (user decides).

Token-tool findings (2026-10-09, from web research): ponytail claims are mostly self-reported (JetBrains saw 1/4-1/2 of the advertised savings); caveman saves output tokens only (65% claimed, 15-25% in one blogger's test); codegraph helps big repos, little on small ones; RTK's `gain` counts removed output, not money (a Terminal-Bench cost test found no saving). Updated on the dev machine: rtk 0.51.0 (brew), codegraph 1.6.2 (npm -g), caveman plugin 3.2.0 (restart needed). tokenwar and minimi rejected; Minimi.app moved to Trash.
