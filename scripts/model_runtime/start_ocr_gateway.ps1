[CmdletBinding()]
param(
    [int]$Port = 8201,
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$EnvironmentPath = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($EnvironmentPath)) {
    $EnvironmentPath = Join-Path $ModelRoot "envs\paddleocr"
}
$python = Join-Path $EnvironmentPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "PaddleOCR environment is missing. Run setup_ocr_gateway.ps1 first."
}
$existing = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw "Port $Port is already listening." }

$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$servicePath = Join-Path $runtimeRoot "ocr_gateway_service.py"
$runtimeData = Join-Path $ModelRoot "ocr\runtime"
$cacheRoot = Join-Path $ModelRoot "ocr\cache"
$logDirectory = Join-Path $ModelRoot "logs\paddleocr"
New-Item -ItemType Directory -Path $runtimeData -Force | Out-Null
New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$env:VIDEO2DOC_OCR_RUNTIME_ROOT = $runtimeData
$env:PADDLE_PDX_CACHE_HOME = $cacheRoot
$env:PADDLE_PDX_MODEL_SOURCE = "BOS"
$env:FLAGS_use_mkldnn = "0"

$stdout = Join-Path $logDirectory "gateway.stdout.log"
$stderr = Join-Path $logDirectory "gateway.stderr.log"
$pidPath = Join-Path $logDirectory "gateway.pid"
$launcherPidPath = Join-Path $logDirectory "gateway.launcher.pid"

# Some managed Windows shells can expose both "Path" and "PATH" in the
# process environment. Start-Process treats them as duplicate dictionary keys
# and fails before launching the child process. Remove only duplicate casings
# while retaining the canonical value.
$processEnvironment = [System.Environment]::GetEnvironmentVariables(
    [System.EnvironmentVariableTarget]::Process
)
$pathKeys = @(
    $processEnvironment.Keys |
        Where-Object {
            [string]::Equals(
                [string]$_,
                "Path",
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }
)
if ($pathKeys.Count -gt 1) {
    $canonicalKey = @($pathKeys | Where-Object { [string]$_ -ceq "Path" })[0]
    if (-not $canonicalKey) { $canonicalKey = $pathKeys[0] }
    $canonicalValue = [string]$processEnvironment[$canonicalKey]
    foreach ($key in $pathKeys) {
        if ([string]$key -cne [string]$canonicalKey) {
            [System.Environment]::SetEnvironmentVariable(
                [string]$key,
                $null,
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }
    [System.Environment]::SetEnvironmentVariable(
        "Path",
        $canonicalValue,
        [System.EnvironmentVariableTarget]::Process
    )
}
$process = Start-Process -FilePath $python -ArgumentList @($servicePath, "--host", "127.0.0.1", "--port", $Port) -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
Set-Content -LiteralPath $launcherPidPath -Value $process.Id -Encoding ASCII

$deadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Where-Object { $_.LocalAddress -in @("127.0.0.1", "0.0.0.0", "::1") } |
                Select-Object -First 1
            if (-not $listener) { throw "Gateway is healthy but no listener PID was found." }
            Set-Content -LiteralPath $pidPath -Value $listener.OwningProcess -Encoding ASCII
            Write-Host "[OK] PaddleOCR Gateway: http://127.0.0.1:$Port"
            Write-Host "Logs: $logDirectory"
            exit 0
        }
    } catch {
        if ($process.HasExited) { throw "Gateway exited early. See $stderr" }
    }
} while ((Get-Date) -lt $deadline)
throw "Gateway did not become healthy. See $stderr"
