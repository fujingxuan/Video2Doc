[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$checker = Join-Path $PSScriptRoot "check_media_tools.ps1"
$windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("Video2Doc media tool tests " + [guid]::NewGuid().ToString("N"))
$originalPath = $env:PATH

function Invoke-Checker {
    param(
        [Parameter(Mandatory = $true)][string]$Ffmpeg,
        [Parameter(Mandatory = $true)][string]$Ffprobe,
        [Parameter(Mandatory = $true)][string]$YtDlp
    )

    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $windowsPowerShell `
            -NoProfile `
            -ExecutionPolicy Bypass `
            -File $checker `
            -Ffmpeg $Ffmpeg `
            -Ffprobe $Ffprobe `
            -YtDlp $YtDlp 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $oldPreference
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = ($output | Out-String)
    }
}

try {
    $null = New-Item -ItemType Directory -Path $tempRoot
    $successShim = Join-Path $tempRoot "delayed success.cmd"
    $failureShim = Join-Path $tempRoot "failure tool.cmd"

    Set-Content -LiteralPath $successShim -Encoding Ascii -Value @(
        "@echo off"
        "echo fake-tool 1.0"
        "ping.exe 127.0.0.1 -n 2 > nul"
        "exit /b 0"
    )
    Set-Content -LiteralPath $failureShim -Encoding Ascii -Value @(
        "@echo off"
        "echo intentional failure 1>&2"
        "exit /b 7"
    )

    $env:PATH = $tempRoot + [System.IO.Path]::PathSeparator + $originalPath

    $success = Invoke-Checker `
        -Ffmpeg $successShim `
        -Ffprobe $successShim `
        -YtDlp (Split-Path -Leaf $successShim)
    if ($success.ExitCode -ne 0) {
        throw "Expected delayed success shims to pass, got exit code $($success.ExitCode):`n$($success.Text)"
    }
    foreach ($name in @("FFmpeg", "ffprobe", "yt-dlp")) {
        if ($success.Text -notmatch ("\[OK\] " + [regex]::Escape($name))) {
            throw "Missing success result for ${name}:`n$($success.Text)"
        }
    }

    $failure = Invoke-Checker `
        -Ffmpeg $successShim `
        -Ffprobe $failureShim `
        -YtDlp (Split-Path -Leaf $successShim)
    if ($failure.ExitCode -ne 1) {
        throw "Expected an unusable tool to fail the checker with exit code 1, got $($failure.ExitCode):`n$($failure.Text)"
    }
    if ($failure.Text -notmatch "\[FAILED\] ffprobe returned exit code 7") {
        throw "Expected the real nonzero exit code to be reported:`n$($failure.Text)"
    }

    Write-Host "check_media_tools.ps1 regression tests passed."
}
finally {
    $env:PATH = $originalPath
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
