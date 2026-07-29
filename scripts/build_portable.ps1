[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot "backend"
$frontend = Join-Path $repoRoot "frontend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $backend ".venv\Scripts\pyinstaller.exe"
$distRoot = Join-Path $repoRoot "dist"
$portable = Join-Path $distRoot "Video2Doc-Portable"
$pyinstallerOutput = Join-Path $distRoot "Video2Doc"
$work = Join-Path $repoRoot "build\pyinstaller"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment is missing. Run scripts\setup.ps1 first."
}
if (-not $SkipInstall) {
    Push-Location $backend
    try {
        & $python -m pip install -e ".[dev]"
        if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
    }
    finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath $pyinstaller)) {
    throw "PyInstaller is missing from the backend virtual environment."
}

Push-Location $frontend
try {
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }
}
finally { Pop-Location }

if (Test-Path -LiteralPath $portable) {
    $resolvedDist = (Resolve-Path -LiteralPath $distRoot).Path
    $resolvedPortable = (Resolve-Path -LiteralPath $portable).Path
    if (-not $resolvedPortable.StartsWith($resolvedDist + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace a portable directory outside dist."
    }
    Remove-Item -LiteralPath $resolvedPortable -Recurse -Force
}
if (Test-Path -LiteralPath $pyinstallerOutput) {
    $resolvedDist = (Resolve-Path -LiteralPath $distRoot).Path
    $resolvedOutput = (Resolve-Path -LiteralPath $pyinstallerOutput).Path
    if (-not $resolvedOutput.StartsWith($resolvedDist + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace a PyInstaller directory outside dist."
    }
    Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
}

New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
New-Item -ItemType Directory -Path $work -Force | Out-Null
$separator = [IO.Path]::PathSeparator
$addFrontend = "$frontend\dist${separator}frontend_dist"
$addMigrations = "$backend\migrations${separator}migrations"
$addAlembic = "$backend\alembic.ini${separator}."

Push-Location $repoRoot
try {
    & $pyinstaller `
        --noconfirm `
        --clean `
        --onedir `
        --windowed `
        --name Video2Doc `
        --distpath $distRoot `
        --workpath $work `
        --specpath $work `
        --paths $backend `
        --add-data $addFrontend `
        --add-data $addMigrations `
        --add-data $addAlembic `
        --hidden-import uvicorn.logging `
        --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto `
        --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan.on `
        (Join-Path $backend "video2doc_launcher.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}
finally { Pop-Location }

if (-not (Test-Path -LiteralPath $pyinstallerOutput)) {
    throw "PyInstaller did not create the expected Video2Doc directory."
}
Move-Item -LiteralPath $pyinstallerOutput -Destination $portable

$readme = @"
Video2Doc Portable
==================

1. Double-click Video2Doc.exe.
2. A standalone Video2Doc window opens automatically.
3. Close that window with X to stop the Video2Doc backend started by it.
4. Starting Video2Doc again while it is running does not open another window.

Application data:
  %LOCALAPPDATA%\Video2Doc

Startup log:
  %LOCALAPPDATA%\Video2Doc\logs\launcher.log

External requirements for real tasks:
  FFmpeg, ffprobe, yt-dlp, and the selected ASR/OCR Gateway.

The application never stops independently managed Docker OCR containers.
"@
Set-Content -LiteralPath (Join-Path $portable "PORTABLE_README.txt") -Value $readme -Encoding UTF8

$archive = Join-Path $distRoot "Video2Doc-Portable.zip"
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
Compress-Archive -LiteralPath $portable -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Portable directory: $portable"
Write-Host "Portable archive: $archive"
