[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
Write-Host "=== Windows ==="
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture,
    @{Name="TotalMemoryGB";Expression={[math]::Round($_.TotalVisibleMemorySize / 1MB, 2)}},
    @{Name="FreeMemoryGB";Expression={[math]::Round($_.FreePhysicalMemory / 1MB, 2)}} | Format-List

Write-Host "=== CPU ==="
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors | Format-List

Write-Host "=== Disk ==="
Get-PSDrive -PSProvider FileSystem | Select-Object Name,
    @{Name="UsedGB";Expression={[math]::Round($_.Used / 1GB, 2)}},
    @{Name="FreeGB";Expression={[math]::Round($_.Free / 1GB, 2)}} | Format-Table -AutoSize

Write-Host "=== NVIDIA ==="
& nvidia-smi

Write-Host "=== WSL2 ==="
& wsl.exe --status
& wsl.exe --list --verbose

Write-Host "=== Docker ==="
& docker version
$dockerInfo = & docker info 2>&1
$dockerInfo | Select-String -Pattern "Server Version|Runtimes|Default Runtime|Total Memory|CPUs"
if ($dockerInfo -match "nvidia") {
    Write-Host "[OK] Docker reports the NVIDIA runtime."
} else {
    Write-Warning "Docker did not report the NVIDIA runtime."
}

Write-Host "=== Python / Conda ==="
& conda info
& python --version

$dashscopeScopes = [pscustomobject]@{
    Process = -not [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)
    User = -not [string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "User")
    )
    Machine = -not [string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "Machine")
    )
}
$dashscopeScopes | Format-List
if (-not ($dashscopeScopes.Process -or $dashscopeScopes.User -or $dashscopeScopes.Machine)) {
    Write-Warning "DASHSCOPE_API_KEY=missing (value was not inspected)"
}

Write-Host "Resource policy: one local ASR model at a time; unload before switching."
