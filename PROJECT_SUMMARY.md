# AgentMesh — project summary

Local CLI (`agentmesh`, Python ≥3.11, only dep PyYAML) that sits above AI coding CLIs. Project **roles** (fixed per repo)
are separated from **workers** (CLIs chosen per run by a router with fallback).

## 1. Layout
`agentmesh/` cli · config · models · errors · security(redact) · discovery · registry · router · runner · prompting · scope ·
worktree · progress · gitutil · task_manager · state · project_analyzer · roles · workflow · initializer · bootstrap · reference · health · fsutil ·
`adapters/` base, claude, codex, antigravity, opencode, cline, kiro, copilot, freebuff, kilo, generic, mock · `templates/` ·
`tests/` (222) · `examples/`.

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
