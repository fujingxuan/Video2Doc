[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"

function Resolve-Python {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { return @($pyLauncher.Source, "-3") }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @($python.Source) }
    throw "Python 3.11 or newer was not found. Install Python and ensure py.exe or python.exe is in PATH."
}

$pythonCommand = Resolve-Python
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating backend virtual environment..."
    $executable = $pythonCommand[0]
    $arguments = @()
    if ($pythonCommand.Count -gt 1) { $arguments += $pythonCommand[1..($pythonCommand.Count - 1)] }
    $arguments += @("-m", "venv", (Join-Path $backendDir ".venv"))
    & $executable @arguments
}

Push-Location $backendDir
try {
    Write-Host "Installing backend dependencies..."
    & $venvPython -m pip install -e ".[dev]"
}
finally { Pop-Location }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 20 or newer was not found in PATH."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm was not found. Install it with: corepack enable"
}

Push-Location $frontendDir
try {
    Write-Host "Installing frontend dependencies..."
    pnpm install
}
finally { Pop-Location }

Write-Host "Checking optional real-media command dependencies..."
$powershellExecutable = (Get-Process -Id $PID).Path
& $powershellExecutable -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_media_tools.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Setup completed, but real media tasks require FFmpeg, ffprobe, and yt-dlp. Configure them before processing real input."
}

Write-Host "Setup complete. Run scripts\start_dev.ps1 from the repository root."
