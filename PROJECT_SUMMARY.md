# AgentMesh — project summary

Local CLI (`agentmesh`, Python ≥3.11, only dep PyYAML) that sits above AI coding CLIs. Project **roles** (fixed per repo)
are separated from **workers** (CLIs chosen per run by a router with fallback).

## 1. Layout
`agentmesh/` cli · config · models · errors · security(redact) · discovery · registry · router · runner · prompting · scope ·
worktree · gitutil · task_manager · state · project_analyzer · roles · workflow · initializer · bootstrap · reference · health · fsutil ·
`adapters/` base, claude, codex, antigravity, qwen, opencode, commandcode, freebuff, kilo, generic, mock · `templates/` ·
`tests/` (219) · `examples/`.

## 2. Key decisions
* Adapters emit only flags found in the CLI's own `--help`; state READY/UNVERIFIED/INTERACTIVE_ONLY/CONFIGURATION_REQUIRED.
* `command-code --version` self-updates the CLI → never probed.
* Generated files carry a content hash: human-edited files are `kept`; project.yaml splits machine vs human keys.
* Changed files always from git; scope globs enforced after each run; fallback only for infra errors.
* Verification is run by AgentMesh (`verify`), `integrate` requires verified.

## 3. Implementation status
Done: discovery, registry, generic+mock adapters, analyzer (Flutter/Node/Python/Go/Rust/JVM), role pack + workflow generation,
init (idempotent), manager bootstrap (CLAUDE.md/AGENTS.md; QWEN on `--manager`), router (4 strategies), runner with fallback,
worktrees, plan/delegate/continue/diff/verify/integrate/status/clean/configure/launch/doctor/smoke, README/CONTRIBUTING/LICENSE.
Not done / unverified: no live model call ever made (use `agentmesh smoke <w> --yes`); antigravity prompt-as-value form inferred;
qwen headless edit approval unknown; Windows untested; keyword-based task classification.
Installed globally on dev machine with `uv tool install -e .`.

Added after MVP: model chains per worker (opencode/kilo/kiro), limit-aware cooldowns (parses reset time), stacked tasks (--base-task), parallel run-batch, enforcement (Claude hooks pre-edit/stop/session-start, audit, abandon). Fixed scope.py dotfile glob bug (.env never matched). Workers: claude codex antigravity qwen opencode command-code cline kiro copilot kilo (freebuff interactive-only; gemini removed).

Benchmark (agentmesh benchmark, live): results in ~/.agentmesh/benchmark.json; init uses them for model chains + quality_order. Open items: antigravity scored 38% (check its --print prompt form / default model); opencode seeded models were still being measured when the session ended (re-run `agentmesh benchmark --workers opencode --yes` if benchmark.json lacks them); tasks too easy to separate the top group; qwen needs login; command-code quota exhausted.

## 4. Next ideas
Live smoke per worker + store results; parse more structured outputs; parallel runner; semantic task classification; `agentmesh adopt` for existing agent packs.
