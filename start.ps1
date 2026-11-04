<#
  Start script for the Deal Desk swarm: sets everything up (only what is missing) and opens the UI.

  Setup:   copy .env.example to .env and put your ANTHROPIC_API_KEY into it (.env is gitignored).
  Run:     powershell -ExecutionPolicy Bypass -File .\start.ps1      (or double-click start.cmd)

  Options:
    -UiOnly     skip all setup, just start the UI
    -NoCritic   don't add the critic agent (stretch S2)
    -Rebuild    delete .coordinator_id and re-create the coordinator (e.g. after changing skills or prompts)

  Each step only runs when its state file is missing, so re-running is safe and creates no duplicates.
  Changed the critic or coordinator prompt? Run with -Rebuild once (re-creates the coordinator and
  re-applies the critic, which also updates the existing critic's prompt).
#>
param(
    [switch]$UiOnly,
    [switch]$NoCritic,
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

# --- load .env (KEY=VALUE per line, # comments allowed) ---
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env" -Encoding UTF8) {
        # comment lines start with "#" and therefore never match the KEY=VALUE pattern
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $value = $Matches[2].Trim('"').Trim("'")
            Set-Item -Path "Env:$($Matches[1])" -Value $value
        }
    }
}

if (-not $env:ANTHROPIC_API_KEY -or $env:ANTHROPIC_API_KEY -eq "sk-ant-...") {
    Write-Host "ANTHROPIC_API_KEY fehlt." -ForegroundColor Red
    Write-Host "Kopiere .env.example nach .env und trage dort deinen Key ein." -ForegroundColor Yellow
    exit 1
}

function Step($title, $script) {
    Write-Host "`n=== $title ===" -ForegroundColor Cyan
    python $script
    if ($LASTEXITCODE -ne 0) { Write-Host "Fehler bei: $script" -ForegroundColor Red; exit $LASTEXITCODE }
}

if (-not $UiOnly) {
    if ($Rebuild -and (Test-Path ".coordinator_id")) { Remove-Item ".coordinator_id" }
    $coordinatorNeeded = -not (Test-Path ".coordinator_id")

    if (-not (Test-Path ".environment_id"))      { Step "Environment anlegen" "setup_environment.py" }
    if (-not (Test-Path ".specialist_ids.json")) { Step "Specialists anlegen" "create_specialists.py"; $coordinatorNeeded = $true }
    if (-not (Test-Path ".skill_ids.json")) {
        Step "Skills hochladen" "upload_skills.py"
        $coordinatorNeeded = $true   # coordinator has to pick up the freshly uploaded firm-voice skill
    }
    if ($coordinatorNeeded)                      { Step "Coordinator anlegen" "create_coordinator.py" }
    if (-not $NoCritic) {
        # stretch_critic_subagent.py exits with code 1 when the coordinator already has the critic,
        # so only run it for a NEW coordinator (it doesn't know the critic yet) or when no critic exists.
        $criticKnown = (Test-Path ".specialist_ids.json") -and ((Get-Content ".specialist_ids.json" -Raw) -match '"critic"')
        if ($coordinatorNeeded -or -not $criticKnown) { Step "Critic einbauen" "stretch_critic_subagent.py" }
    }
}

Write-Host "`n=== UI starten: http://127.0.0.1:8000 (Strg+C zum Beenden) ===" -ForegroundColor Green
Start-Process "http://127.0.0.1:8000"
python ui/app.py
