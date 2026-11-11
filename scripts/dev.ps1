# AI Personal Classroom — developer helper commands (Windows / PowerShell).
# Usage:  pwsh -File scripts/dev.ps1 <command>
param(
  [Parameter(Position = 0)]
  [ValidateSet('setup', 'backend', 'test', 'flutter', 'flutter-test', 'all', 'reset-db')]
  [string]$Command = 'setup'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Backend = Join-Path $Root 'backend'
$App = Join-Path $Root 'app'

function Ensure-Venv {
  if (-not (Test-Path $Py)) {
    Write-Host 'Creating virtualenv...' -ForegroundColor Cyan
    python -m venv $Venv
    & $Py -m pip install --upgrade pip | Out-Null
  }
  & $Py -m pip install -q -r (Join-Path $Backend 'requirements.txt')
}

switch ($Command) {
  'setup' {
    Ensure-Venv
    if (-not (Test-Path (Join-Path $Root '.env'))) {
      Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
      Write-Host 'Created .env from .env.example' -ForegroundColor Green
    }
    Write-Host 'Backend ready. Run: pwsh -File scripts/dev.ps1 backend' -ForegroundColor Green
  }
  'backend' {
    Ensure-Venv
    Push-Location $Backend
    try {
      & $Py -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    }
    finally { Pop-Location }
  }
  'test' {
    Push-Location $Backend
    try { & $Py -m pytest -q } finally { Pop-Location }
  }
  'flutter' {
    Push-Location $App
    try { flutter pub get; flutter run } finally { Pop-Location }
  }
  'flutter-test' {
    Push-Location $App
    try { flutter analyze; flutter test } finally { Pop-Location }
  }
  'reset-db' {
    $db = Join-Path $Backend 'dev.db'
    if (Test-Path $db) { Remove-Item $db -Force; Write-Host 'dev.db removed' -ForegroundColor Yellow }
  }
  'all' { Ensure-Venv; & $PSCommandPath test }
}
