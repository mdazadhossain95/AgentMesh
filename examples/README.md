# Examples

Each folder is real `agentmesh init --auto --yes` output against a tiny synthetic repo for that stack (not hand-written).
Read one to see the Agent Pack shape (roles, workflow, project.yaml, CLAUDE.md managed section) before running init on
your own project. The synthetic source files themselves aren't committed, only what `init` generated from them; the
same repos are kept live as pytest fixtures in `tests/conftest.py` (`android_native`, `react_web`, `node_backend`,
`ios_native`, plus the existing `flutter_only`/`flutter_backend`) and exercised in `tests/test_multi_stack.py`.

- `flutter-backend/` — Flutter app + FastAPI `backend/` (9 roles: app-engineer, backend-engineer, contract-keeper,
  security-reviewer, ...).
- `android-native/` — standalone Kotlin/Gradle app, no Flutter (6 roles: app-engineer handles `app/**`).
- `react-web/` — React + Vite + TypeScript SPA, no backend (6 roles: frontend-engineer).
- `node-backend/` — Express + Postgres API, no frontend (6 roles: backend-engineer).
- `ios-native/` — Swift/Xcode + CocoaPods app. **Known gap**: the analyzer has no Podfile/.xcodeproj detector yet,
  so this one gets zero components and falls back to the generic single `software-engineer` role instead of a real
  `app-engineer`. Kept here deliberately so the gap is visible rather than silently "working"; see
  `test_ios_native_is_not_detected_generic_fallback_only` in `tests/test_multi_stack.py`.
- `agents.yaml` — register an extra CLI through the generic adapter (no code).
