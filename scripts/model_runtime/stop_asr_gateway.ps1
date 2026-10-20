[CmdletBinding()]
param(
    [ValidateSet("firered", "qwen", "whisper", "mock")]
    [string]$Provider,
    [int]$Port = 0,
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$Distribution = "Ubuntu"
)

$ErrorActionPreference = "Continue"
$ports = @{ firered = 8101; qwen = 8102; whisper = 8103; mock = 8199 }
if ($Port -eq 0) { $Port = $ports[$Provider] }
$pidPath = Join-Path $ModelRoot "logs\$Provider\gateway.pid"
if (Test-Path -LiteralPath $pidPath) {
    $processId = [int](Get-Content -LiteralPath $pidPath -Raw)
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
& wsl.exe -d $Distribution -- bash -lc "pkill -f 'asr_gateway_service.py --host 127.0.0.1 --port $Port' || true"
$deadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
} while ($listener -and (Get-Date) -lt $deadline)
if ($listener) {
    throw "Port $Port is still listening after stop."
}
Write-Host "[OK] Stopped $Provider ASR Gateway on port $Port"
