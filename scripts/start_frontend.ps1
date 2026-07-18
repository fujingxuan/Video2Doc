[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot "frontend"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 20 or newer was not found in PATH."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm was not found. Install it or run corepack enable."
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules"))) {
    throw "Frontend dependencies are missing. Run .\scripts\setup.ps1 first."
}

Push-Location $frontendDir
try {
    Write-Host "Starting Video2Doc frontend at http://127.0.0.1:5173"
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        pnpm dev --host 127.0.0.1 --strictPort 2>&1
        $viteExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($viteExitCode -ne 0) {
        throw "Vite exited with code $viteExitCode."
    }
}
finally { Pop-Location }
