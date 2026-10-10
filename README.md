# AgentMesh

Use all your AI coding CLIs (Claude Code, Codex, Kiro, Copilot, OpenCode, ...) on one project, as one team.

* It reads your project and creates the right **agents** (backend, app, web, tester, reviewer, ...) as plain Markdown files.
* When one CLI runs out of quota, the same task moves to the next CLI. No work lost.
* Every task runs in its own git worktree. AgentMesh checks the real git diff and runs your tests before anything merges.

```
you ──▶ manager (the CLI you open)
          │  agentmesh plan / delegate / verify / integrate
          ▼
   agent (fixed by the project)        CLI that runs it (chosen per run)
   backend-engineer  ───────────────▶  codex ──quota──▶ kiro ──▶ opencode
```

## 1. Install (one line)

macOS / Linux:
```bash
curl -LsSf https://raw.githubusercontent.com/mdazadhossain95/AgentMesh/main/install.sh | sh
```
Windows (PowerShell):
```powershell
powershell -c "irm https://raw.githubusercontent.com/mdazadhossain95/AgentMesh/main/install.ps1 | iex"
```
Open a new terminal, then check: `agentmesh --help`. Run the same line again to update.

Prefer to read the code first? `git clone https://github.com/mdazadhossain95/AgentMesh && cd AgentMesh && uv tool install -e .`
(Python 3.11+ and git needed.) Details: [docs/REFERENCE.md](docs/REFERENCE.md).

Log in to each AI CLI once, the normal way (`claude`, `codex`, `kiro-cli`, ...). AgentMesh never logs in for you.

## 2. Install and log in to your AI CLIs (guided)

```bash
agentmesh setup
```

It goes through your CLIs one at a time and asks before each step:

* **Not installed:** shows the install command (or the official page) and asks "Install now?".
* **Installed, not logged in:** asks "Log in now?" and opens that CLI's own login. You finish it in the browser.
* **Logged in:** nothing to do.

AgentMesh never sees your password or token, and nothing runs without your yes. One logged-in CLI is enough to start.
`agentmesh setup --check` only reports and changes nothing.

## 3. Use it in any project (one paste)

```bash
cd ~/Projects/MyApp
agentmesh init --auto --yes && agentmesh doctor && agentmesh launch claude
```

Then tell the manager what to build: *"Add the profile API and the profile screen."*
It plans, delegates, checks the diff, runs tests, and merges only verified work.

`init` creates this (commit it, your team shares it):

```
MyApp/
├── CLAUDE.md, AGENTS.md       # short instructions for the manager CLI
└── .agentmesh/
    ├── project.yaml           # what was detected + routing/fallback policy
    ├── workflow.yaml          # which agent runs when
    └── agents/*.md            # one file per agent, plain Markdown, edit freely
```

Agents are picked from what the repo contains: a Flutter app gets `app-engineer`, a backend gets `backend-engineer`,
a web app gets `frontend-engineer`, API contracts get `contract-keeper`; every project gets `spec-analyst`,
`test-executor`, `failure-triage`, `reviewer`. Security agents appear only when auth/payment signals exist.

## 4. Everyday commands

| Command | What it does |
|---|---|
| `agentmesh setup` | Install missing CLIs and log in, one at a time |
| `agentmesh discover` | Which AI CLIs are installed here |
| `agentmesh init --auto` | Analyze the repo, create/refresh `.agentmesh/` (never overwrites your edits) |
| `agentmesh doctor` | Health check, no model calls |
| `agentmesh plan "<request>"` | Which agents would run, in what order |
| `agentmesh status` | Workers and tasks |
| `agentmesh launch <cli>` | Open a CLI as manager |

All commands and flags: [docs/REFERENCE.md](docs/REFERENCE.md).

## 5. Change an agent

Open `.agentmesh/agents/<name>.md` and edit it. `init` keeps your edits (it only rewrites files you did not touch).
Change which files an agent may edit without touching the file:

```bash
agentmesh configure set overrides.roles.backend-engineer.allowed_paths '["server/**","migrations/**"]'
```

## 6. Add your own AI CLI (no code)

Any CLI that takes a prompt and edits files can be a worker:

```bash
agentmesh configure add-agent mytool --executable mytool --headless-arg=--print --headless-arg='{prompt}'
agentmesh discover          # it now shows up
agentmesh smoke mytool --yes   # one tiny live call to confirm it works (may use quota)
```

Placeholders: `{prompt} {cwd} {model} {timeout}`. Optional: `--edit-arg`, `--full-arg`, `--continue-arg`, `--stdin-prompt`.
Need parsing or special errors? Write a small adapter class: see [CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/REFERENCE.md](docs/REFERENCE.md#adapter-development).

## 7. Save tokens

* Cheap CLIs first: set `routing.strategy: free-first` and list yours in `routing.free_workers`.
* `init` and `doctor` suggest token tools that fit the project (RTK, caveman, CodeGraph) and ask before setting anything up.
  Their saving claims are mostly self-reported; treat them as directional.
* Risk tiers: low-risk changes get a light check, auth/API/database changes get an extra review.
* Expensive commands (`benchmark`, `smoke`) never run unless you pass `--yes`.

## What is verified

280 offline tests, CI green on Ubuntu, macOS and Windows. Live smoke calls passed on Claude Code, Codex, Kiro,
Copilot and OpenCode (macOS). Not verified: real worker CLIs on Windows and Linux, `install.ps1`. This is an early
version. Details and known limits: [docs/REFERENCE.md](docs/REFERENCE.md).

## License

MIT
