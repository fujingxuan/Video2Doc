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
        $command = Get-Command $Executable -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($command) { $resolved = $command.Path }
    }
    if (-not $resolved) {
        Write-Host "[MISSING] $Name ($Executable)"
        $script:missing += $Name
        return
    }
    # Windows PowerShell 5.1 can set $LASTEXITCODE to -1 when a downstream
    # Select-Object -First stops a native-process pipeline before it is fully
    # drained. Buffer the complete output first so the process has exited before
    # capturing its exit code, then select the display line from memory.
    $global:LASTEXITCODE = $null
    $toolOutput = @(& $resolved @Arguments 2>&1)
    $exitCode = $global:LASTEXITCODE
    $firstOutputLine = $toolOutput | Select-Object -First 1

    if ($null -eq $exitCode) {
        Write-Host "[FAILED] $Name could not be started"
        if ($null -ne $firstOutputLine) { Write-Host "         $firstOutputLine" }
        $script:missing += $Name
        return
    }
    if ($exitCode -ne 0) {
        Write-Host "[FAILED] $Name returned exit code $exitCode"
        if ($null -ne $firstOutputLine) { Write-Host "         $firstOutputLine" }
        $script:missing += $Name
        return
    }
    if ($null -ne $firstOutputLine) {
        Write-Host "[OK] $Name - $firstOutputLine"
    }
    else {
        Write-Host "[OK] $Name"
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
