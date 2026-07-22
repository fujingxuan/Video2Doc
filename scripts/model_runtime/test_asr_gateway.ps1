[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AudioPath,
    [Parameter(Mandatory=$true)][string]$Model,
    [int]$Port = 8103,
    [string]$Language = "zh"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $AudioPath -PathType Leaf)) { throw "Audio file not found: $AudioPath" }
& curl.exe --fail --silent --show-error "http://127.0.0.1:$Port/v1/asr/transcriptions" `
    -F "file=@$AudioPath;type=audio/wav" `
    -F "model=$Model" `
    -F "language=$Language" `
    -F "timestamp_granularity=word"
if ($LASTEXITCODE -ne 0) { throw "ASR Gateway inference request failed." }
