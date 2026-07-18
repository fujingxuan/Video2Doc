[CmdletBinding()]
param(
    [string]$Ffmpeg = "ffmpeg",
    [string]$Ffprobe = "ffprobe",
    [string]$YtDlp = "yt-dlp"
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest
$missing = @()

function Test-ExternalTool {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $resolved = $null
    if ([System.IO.Path]::IsPathRooted($Executable)) {
        if (Test-Path -LiteralPath $Executable -PathType Leaf) { $resolved = $Executable }
    }
    else {
        $command = Get-Command $Executable -ErrorAction SilentlyContinue
        if ($command) { $resolved = $command.Source }
    }
    if (-not $resolved) {
        Write-Host "[MISSING] $Name ($Executable)"
        $script:missing += $Name
        return
    }
    & $resolved @Arguments 2>&1 | Select-Object -First 1 | ForEach-Object {
        Write-Host "[OK] $Name - $_"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAILED] $Name returned exit code $LASTEXITCODE"
        $script:missing += $Name
    }
}

Test-ExternalTool -Name "FFmpeg" -Executable $Ffmpeg -Arguments @("-version")
Test-ExternalTool -Name "ffprobe" -Executable $Ffprobe -Arguments @("-version")
Test-ExternalTool -Name "yt-dlp" -Executable $YtDlp -Arguments @("--version")

if ($missing.Count -gt 0) {
    Write-Error "Missing or unusable media tools: $($missing -join ', '). Install them or configure their executable paths in Video2Doc settings."
    exit 1
}
Write-Host "All real-media command dependencies are available."
