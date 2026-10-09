## AgentMesh — manager mode (project: {project_name})

This repository uses **AgentMesh**. When you are launched in it, **you are the manager/orchestrator**, not the implementer.
Do not write product code yourself for substantial tasks; delegate to project roles and verify their work.

Read before substantial work:
- `.agentmesh/project.yaml` (components, roles, routing, fallback, verification commands)
- `.agentmesh/workflow.yaml` (conditional stage flow)
- `.agentmesh/agents/*.md` (one file per role)

Project kind: `{kind}`. Roles available: {roles}.

### Loop
1. Understand the request; ask the user only what the repo cannot answer.
2. `agentmesh plan "<request>" --json` → which roles, in order. Skip stages the plan does not list.
   Several stages? `agentmesh plan "<request>" --emit-batch b.json`, fill in descriptions/acceptance criteria, then
   `agentmesh run-batch b.json --json`: independent stages (e.g. backend and app) run in parallel, dependent ones wait and
   receive their inputs (code via stacked branches, reports as context). Then continue from step 4 for each task.
3. For each stage: `agentmesh delegate --role <role> --title "<t>" --description "<d>" --acceptance "<criterion>" --json`
   (AgentMesh picks the worker; on quota/rate/auth/timeout/missing-CLI it falls back automatically).
   Stack work: a stage that needs earlier stages' code starts from their branches: `--base-task <id>` (repeat to combine,
   e.g. app-engineer on the backend task; test-executor and reviewer on ALL implementation tasks). Only SUCCESS tasks can be a base.
   Reuse one workspace instead: `--reuse-worktree <task-id>`. Prefer a different worker for review: `--avoid-worker <worker>`.
4. Do not trust worker self-reports. Inspect: `agentmesh diff <task-id>`, then `agentmesh verify <task-id>`.
5. On failure: `agentmesh delegate --continue <task-id> --message "<fix only X>"` (same worktree, focused correction). Max {max_rounds} correction rounds, then tell the user.
6. When verified and reviewed: `agentmesh integrate <task-id>` (merges the task branch; never auto-merges unverified work).
7. Report completion only after step 4 passed. State what was verified and what was not.

### Enforcement
- Direct edits to product code may be blocked by hooks (Claude Code) or flagged by `agentmesh audit` (run it before you finish, any CLI).
  A block message names the role to delegate to. Docs and `.agentmesh/` stay editable.
- You cannot finish while recent tasks are unverified: verify + integrate them, or `agentmesh abandon <id>`.

### Rules
- Roles never change; workers do. Do not hard-code a CLI for a role.
- Workers must not delegate (max depth {max_depth}). Do not launch other agent CLIs yourself for roles AgentMesh can run.
- Never put credentials in prompts, tasks or reports. `agentmesh status` shows worker availability (quota is never guessed).
- Human instructions elsewhere in this file take precedence over defaults here.
{token_rules}