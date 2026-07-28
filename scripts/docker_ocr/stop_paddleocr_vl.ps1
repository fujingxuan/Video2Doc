[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "..\..\deploy\docker\paddleocr-vl\compose.yaml"
$compose = (Resolve-Path -LiteralPath $compose).Path

$managed = @(
    docker ps -a `
        --filter "label=com.video2doc.managed=true" `
        --filter "name=video2doc-paddleocr-" `
        --format "{{.Names}}"
)
if (-not $managed) {
    Write-Host "No Video2Doc-managed PaddleOCR containers are present."
    exit 0
}
docker compose -f $compose -p video2doc-paddleocr down
if ($LASTEXITCODE -ne 0) { throw "Video2Doc PaddleOCR services failed to stop." }
Write-Host "Stopped only Video2Doc-managed PaddleOCR containers. External volume was preserved."
