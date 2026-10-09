#!/bin/sh
# AgentMesh installer for macOS and Linux.
#   curl -LsSf https://raw.githubusercontent.com/mdazadhossain95/AgentMesh/main/install.sh | sh
# Installs uv if missing, then installs the global `agentmesh` command from GitHub. Safe to run again (it updates).
set -eu

REPO="git+https://github.com/mdazadhossain95/AgentMesh"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required. Install it first (macOS: xcode-select --install; Linux: your package manager)." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (the Python tool installer)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export PATH
fi

echo "Installing AgentMesh..."
uv tool install --force "$REPO"
uv tool update-shell >/dev/null 2>&1 || true

echo
echo "Done. Open a NEW terminal, then:"
echo "  agentmesh --help"
echo "  cd your-project && agentmesh init --auto --yes && agentmesh launch claude"
