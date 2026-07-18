[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$backendScript = Join-Path $PSScriptRoot "start_backend.ps1"
$frontendScript = Join-Path $PSScriptRoot "start_frontend.ps1"

Write-Host "Starting backend and frontend as PowerShell jobs. Press Ctrl+C to stop both."
$backendJob = Start-Job -Name "Video2Doc-Backend" -ScriptBlock {
    param($scriptPath)
    & $scriptPath
} -ArgumentList $backendScript
$frontendJob = Start-Job -Name "Video2Doc-Frontend" -ScriptBlock {
    param($scriptPath)
    & $scriptPath
} -ArgumentList $frontendScript

try {
    while ($backendJob.State -in @("NotStarted", "Running") -and $frontendJob.State -in @("NotStarted", "Running")) {
        Receive-Job -Job $backendJob, $frontendJob
        Start-Sleep -Milliseconds 500
        $backendJob = Get-Job -Id $backendJob.Id
        $frontendJob = Get-Job -Id $frontendJob.Id
    }
    Receive-Job -Job $backendJob, $frontendJob
    if ($backendJob.State -eq "Failed") { throw "Backend job failed. Review the output above." }
    if ($frontendJob.State -eq "Failed") { throw "Frontend job failed. Review the output above." }
}
finally {
    Stop-Job -Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job -Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
}
