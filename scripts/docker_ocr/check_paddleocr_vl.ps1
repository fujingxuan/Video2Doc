[CmdletBinding()]
param(
    [int]$ApiPort = 8202,
    [int]$VlmPort = 8118
)

$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "..\..\deploy\docker\paddleocr-vl\compose.yaml"
$compose = (Resolve-Path -LiteralPath $compose).Path

docker compose -f $compose -p video2doc-paddleocr ps
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Video2Doc PaddleOCR services." }

foreach ($entry in @(
    @{ Name = "PaddleOCR-VL API"; Uri = "http://127.0.0.1:$ApiPort/health" },
    @{ Name = "PaddleOCR-VL VLM"; Uri = "http://127.0.0.1:$VlmPort/health" }
)) {
    try {
        $response = Invoke-RestMethod -Uri $entry.Uri -TimeoutSec 10
        Write-Host "[OK] $($entry.Name) $($entry.Uri)"
        $response | ConvertTo-Json -Compress -Depth 5
    } catch {
        Write-Host "[UNAVAILABLE] $($entry.Name) $($entry.Uri): $($_.Exception.Message)"
    }
}
