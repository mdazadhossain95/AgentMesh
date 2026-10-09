# AgentMesh installer for Windows (PowerShell).
#   powershell -c "irm https://raw.githubusercontent.com/mdazadhossain95/AgentMesh/main/install.ps1 | iex"
# Installs uv if missing, then installs the global `agentmesh` command from GitHub. Safe to run again (it updates).
$ErrorActionPreference = "Stop"
$Repo = "git+https://github.com/mdazadhossain95/AgentMesh"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git is required. Install it first: winget install Git.Git"
    exit 1
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (the Python tool installer)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "Installing AgentMesh..."
uv tool install --force $Repo
uv tool update-shell | Out-Null

Write-Host ""
Write-Host "Done. Open a NEW terminal, then:"
Write-Host "  agentmesh --help"
Write-Host "  cd your-project; agentmesh init --auto --yes; agentmesh launch claude"
