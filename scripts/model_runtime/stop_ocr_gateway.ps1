[CmdletBinding()]
param(
    [int]$Port = 8201,
    [string]$ModelRoot = "D:\Models\Video2Doc"
)

$ErrorActionPreference = "Continue"
function Stop-ProcessTree {
    param([int]$ProcessId)
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId $child.ProcessId
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$logDirectory = Join-Path $ModelRoot "logs\paddleocr"
$pidPaths = @(
    (Join-Path $logDirectory "gateway.pid"),
    (Join-Path $logDirectory "gateway.launcher.pid")
)
$processIds = @()
foreach ($pidPath in $pidPaths) {
    if (Test-Path -LiteralPath $pidPath) {
        $rawProcessId = Get-Content -LiteralPath $pidPath -Raw
        if ($rawProcessId -match "^\s*\d+\s*$") {
            $processIds += [int]$rawProcessId
        }
    }
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($listenerProcess.CommandLine -like "*ocr_gateway_service.py*") {
        $processIds += [int]$listener.OwningProcess
    }
}
foreach ($processId in ($processIds | Sort-Object -Unique)) {
    Stop-ProcessTree -ProcessId $processId
}
foreach ($pidPath in $pidPaths) {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
} while ($listener -and (Get-Date) -lt $deadline)
if ($listener) {
    throw "Port $Port is still listening after stop."
}
Write-Host "[OK] Stopped PaddleOCR Gateway on port $Port"
