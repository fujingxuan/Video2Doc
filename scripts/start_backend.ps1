[CmdletBinding()]
param(
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$python = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment is missing. Run .\scripts\setup.ps1 first."
}

Push-Location $backendDir
try {
    Write-Host "Applying database migrations..."
    & $python -m alembic upgrade head
    $arguments = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
    if (-not $NoReload) { $arguments += "--reload" }
    Write-Host "Starting Video2Doc API at http://127.0.0.1:8000"
    & $python @arguments
}
finally { Pop-Location }
