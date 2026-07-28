[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "..\..\deploy\docker\paddleocr-vl\compose.yaml"
$compose = (Resolve-Path -LiteralPath $compose).Path

$volume = docker volume inspect paddleocr_model_cache --format "{{.Name}}" 2>$null
if ($LASTEXITCODE -ne 0 -or $volume -ne "paddleocr_model_cache") {
    throw "Required external volume paddleocr_model_cache was not found. Nothing was created."
}

docker compose -f $compose -p video2doc-paddleocr up -d
if ($LASTEXITCODE -ne 0) { throw "Video2Doc PaddleOCR services failed to start." }
Write-Host "Video2Doc-managed PaddleOCR services are starting."
Write-Host "API: http://127.0.0.1:8202"
Write-Host "VLM: http://127.0.0.1:8118"
