[CmdletBinding()]
param(
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$BootstrapPython = "",
    [string]$EnvironmentPath = ""
)

$ErrorActionPreference = "Stop"
$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $runtimeRoot)
if ([string]::IsNullOrWhiteSpace($BootstrapPython)) {
    $BootstrapPython = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($EnvironmentPath)) {
    $EnvironmentPath = Join-Path $ModelRoot "envs\paddleocr"
}
if (-not (Test-Path -LiteralPath $BootstrapPython -PathType Leaf)) {
    throw "Bootstrap Python is missing: $BootstrapPython"
}

$python = Join-Path $EnvironmentPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $EnvironmentPath) -Force | Out-Null
    & $BootstrapPython -m venv $EnvironmentPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create PaddleOCR virtual environment." }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }
& $python -m pip install "paddlepaddle==3.3.0" -i "https://www.paddlepaddle.org.cn/packages/stable/cpu/"
if ($LASTEXITCODE -ne 0) { throw "Failed to install PaddlePaddle 3.3.0 CPU." }
& $python -m pip install -r (Join-Path $runtimeRoot "requirements-ocr.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install PaddleOCR Gateway dependencies." }
& $python -c "import paddle; paddle.utils.run_check(); import paddleocr; print('PaddleOCR', paddleocr.__version__)"
if ($LASTEXITCODE -ne 0) { throw "PaddleOCR runtime verification failed." }

New-Item -ItemType Directory -Path (Join-Path $ModelRoot "ocr\cache") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ModelRoot "ocr\runtime\temp") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ModelRoot "logs\paddleocr") -Force | Out-Null
Write-Host "[OK] PaddleOCR environment: $EnvironmentPath"
Write-Host "[OK] Model/cache root: $(Join-Path $ModelRoot 'ocr')"
