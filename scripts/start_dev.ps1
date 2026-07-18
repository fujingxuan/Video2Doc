[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$backendScript = Join-Path $PSScriptRoot "start_backend.ps1"
$frontendScript = Join-Path $PSScriptRoot "start_frontend.ps1"
$supervisorProcessId = $PID

function Get-DescendantProcessIds {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ParentProcessId
    )

    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        try {
            $processes = @(Get-WmiObject Win32_Process -ErrorAction Stop)
        }
        catch {
            Write-Warning "Could not inspect child processes during shutdown: $($_.Exception.Message)"
            return @()
        }
    }

    $descendants = @()
    $parents = @($ParentProcessId)
    while ($parents.Count -gt 0) {
        $children = @(
            $processes | Where-Object {
                $_.ParentProcessId -in $parents -and $_.ProcessId -notin $descendants
            }
        )
        if ($children.Count -eq 0) { break }
        $childIds = @($children | ForEach-Object { [int]$_.ProcessId })
        $descendants += $childIds
        $parents = $childIds
    }
    return @($descendants)
}

function Receive-ServiceJobOutput {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.Job]$Job,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    # Windows PowerShell turns native stderr received from a background job
    # into ErrorRecords. Alembic, Uvicorn, and Vite legitimately log there, so
    # display that stream without letting $ErrorActionPreference stop this
    # supervisor. The job state and native exit code remain the failure signal.
    $jobErrors = @()
    $jobOutput = @(
        Receive-Job -Job $Job -ErrorAction SilentlyContinue -ErrorVariable jobErrors
    )
    foreach ($item in $jobOutput) {
        Write-Host "[$Label] $item"
    }
    foreach ($errorRecord in $jobErrors) {
        [Console]::Error.WriteLine("[$Label] {0}", $errorRecord.ToString())
    }
}

function Get-ServiceExitMessage {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.Job]$Job,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if ($Job.State -eq "Failed") {
        $reason = $Job.ChildJobs[0].JobStateInfo.Reason
        if ($null -ne $reason -and -not [string]::IsNullOrWhiteSpace($reason.Message)) {
            return "$Label failed: $($reason.Message)"
        }
        return "$Label failed. Review the prefixed output above."
    }
    return "$Label exited unexpectedly (job state: $($Job.State))."
}

Write-Host "Starting backend and frontend as PowerShell jobs. Press Ctrl+C to stop both."
$backendJob = Start-Job -Name "Video2Doc-Backend" -ScriptBlock {
    param($scriptPath)
    # Directly supervise the Uvicorn process. Its reload supervisor can remain
    # alive after a child server fails, which would hide a real backend exit.
    & $scriptPath -NoReload
} -ArgumentList $backendScript
$frontendJob = Start-Job -Name "Video2Doc-Frontend" -ScriptBlock {
    param($scriptPath)
    & $scriptPath
} -ArgumentList $frontendScript

$exitMessage = $null
$exitedJobId = $null
try {
    while ($true) {
        Receive-ServiceJobOutput -Job $backendJob -Label "Backend"
        Receive-ServiceJobOutput -Job $frontendJob -Label "Frontend"

        $backendJob = Get-Job -Id $backendJob.Id
        $frontendJob = Get-Job -Id $frontendJob.Id
        if ($backendJob.State -notin @("NotStarted", "Running")) {
            $exitMessage = Get-ServiceExitMessage -Job $backendJob -Label "Backend"
            $exitedJobId = $backendJob.Id
            break
        }
        if ($frontendJob.State -notin @("NotStarted", "Running")) {
            $exitMessage = Get-ServiceExitMessage -Job $frontendJob -Label "Frontend"
            $exitedJobId = $frontendJob.Id
            break
        }
        Start-Sleep -Milliseconds 500
    }
}
finally {
    if ($backendJob.Id -ne $exitedJobId) {
        Receive-ServiceJobOutput -Job $backendJob -Label "Backend"
    }
    if ($frontendJob.Id -ne $exitedJobId) {
        Receive-ServiceJobOutput -Job $frontendJob -Label "Frontend"
    }
    Write-Host "Stopping Video2Doc backend and frontend..."
    $descendantProcessIds = @(Get-DescendantProcessIds -ParentProcessId $supervisorProcessId)
    if ($descendantProcessIds.Count -gt 0) {
        Stop-Process -Id $descendantProcessIds -Force -ErrorAction SilentlyContinue
    }
    Stop-Job -Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Wait-Job -Job $backendJob, $frontendJob -Timeout 5 -ErrorAction SilentlyContinue | Out-Null
    Remove-Job -Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
}

if ($null -ne $exitMessage) {
    throw "$exitMessage The other service was stopped."
}
