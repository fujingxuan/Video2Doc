[CmdletBinding()]
param(
    [ValidateSet("firered", "qwen", "whisper", "mock")]
    [string]$Provider,
    [int]$Port = 0,
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$EnvironmentRoot = "",
    [string]$Distribution = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$ports = @{ firered = 8101; qwen = 8102; whisper = 8103; mock = 8199 }
if ($Port -eq 0) { $Port = $ports[$Provider] }
$existing = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw "Port $Port is already listening." }

$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$servicePath = (& wsl.exe -d $Distribution -- wslpath -a (($runtimeRoot + "\asr_gateway_service.py") -replace "\\", "/")).Trim()
$launcherPath = (& wsl.exe -d $Distribution -- wslpath -a (($runtimeRoot + "\run_asr_gateway.sh") -replace "\\", "/")).Trim()
$wslModelRoot = (& wsl.exe -d $Distribution -- wslpath -a ($ModelRoot -replace "\\", "/")).Trim()
$wslEnvironmentRoot = if ([string]::IsNullOrWhiteSpace($EnvironmentRoot)) {
    (& wsl.exe -d $Distribution -- bash -lc 'printf %s "${XDG_DATA_HOME:-$HOME/.local/share}/video2doc-model-runtime"').Trim()
} elseif ($EnvironmentRoot -match '^[A-Za-z]:\\') {
    (& wsl.exe -d $Distribution -- wslpath -a ($EnvironmentRoot -replace "\\", "/")).Trim()
} else {
    $EnvironmentRoot.TrimEnd('/')
}
$python = if ($Provider -eq "mock") { "python3" } else { "$wslEnvironmentRoot/envs/$Provider/bin/python" }
if ($Provider -ne "mock") {
    & wsl.exe -d $Distribution -- bash -lc "test -x '$python'"
    if ($LASTEXITCODE -ne 0) {
        throw "Missing $Provider Python environment at $python. Run setup_local_models.ps1 first."
    }

    $requiredModelPaths = @{
        firered = @("$wslModelRoot/firered/FireRedASR2-AED")
        qwen = @("$wslModelRoot/qwen/Qwen3-ASR-1.7B", "$wslModelRoot/qwen/Qwen3-ForcedAligner-0.6B")
        whisper = @("$wslModelRoot/whisper/large-v3", "$wslModelRoot/whisper/large-v3-turbo")
    }
    if ($Provider -eq "firered") {
        & wsl.exe -d $Distribution -- bash -lc "test -d '$wslModelRoot/firered/FireRedASR2S/fireredasr2s'"
        if ($LASTEXITCODE -ne 0) {
            throw "Official FireRedASR2S source checkout is missing. Run setup_local_models.ps1 first."
        }
    }
    foreach ($modelPath in $requiredModelPaths[$Provider]) {
        & wsl.exe -d $Distribution -- bash -lc "test -d '$modelPath' && if find '$modelPath' -type f -name '*.incomplete' -print -quit | grep -q .; then exit 2; fi && find '$modelPath' -type f \( -name '*.safetensors' -o -name '*.bin' -o -name '*.pt' -o -name '*.pth' -o -name '*.pth.tar' \) -print -quit | grep -q ."
        if ($LASTEXITCODE -ne 0) {
            throw "Model files are missing or incomplete at $modelPath. Run download_models.ps1 first."
        }
    }

    $gpuLine = & nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1
    # A preceding WSL native command can leave a stale LASTEXITCODE in Windows
    # PowerShell even when this pipeline returned a valid nvidia-smi row.
    if ([string]::IsNullOrWhiteSpace($gpuLine)) {
        throw "NVIDIA GPU status is unavailable. Check the driver and WSL GPU support."
    }
    $gpuParts = @($gpuLine -split ',' | ForEach-Object { $_.Trim() })
    $freeMemoryMiB = [int]$gpuParts[2]
    $recommendedFreeMiB = (@{ firered = 5000; qwen = 7000; whisper = 5000 })[$Provider]
    Write-Host "[OK] GPU: $($gpuParts[0]), total=$($gpuParts[1]) MiB, free=$freeMemoryMiB MiB"
    if ($freeMemoryMiB -lt $recommendedFreeMiB) {
        Write-Warning "$Provider has $freeMemoryMiB MiB free VRAM; recommended free VRAM is at least $recommendedFreeMiB MiB. Startup may fail with OOM."
    }
}
$logDirectory = Join-Path $ModelRoot "logs\$Provider"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$stdout = Join-Path $logDirectory "gateway.stdout.log"
$stderr = Join-Path $logDirectory "gateway.stderr.log"
$pidPath = Join-Path $logDirectory "gateway.pid"
$process = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $Distribution, "--", "bash", $launcherPath, $Provider, $wslModelRoot, $python, $servicePath, $Port) -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII

$deadline = (Get-Date).AddSeconds(600)
do {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "[OK] $Provider ASR Gateway: http://127.0.0.1:$Port"
            Write-Host "Logs: $logDirectory"
            exit 0
        }
    } catch {
        if ($process.HasExited) { throw "Gateway exited early. See $stderr" }
    }
} while ((Get-Date) -lt $deadline)
throw "Gateway did not become healthy. See $stderr"
