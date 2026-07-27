[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [string]$Output = "",
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$EnvironmentPath = "",
    [int]$Port = 8201
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($EnvironmentPath)) {
    $EnvironmentPath = Join-Path $ModelRoot "envs\paddleocr"
}
$python = Join-Path $EnvironmentPath "Scripts\python.exe"
$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @((Join-Path $runtimeRoot "test_ocr_gateway.py"), $Image, "--base-url", "http://127.0.0.1:$Port")
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $arguments += @("--output", $Output)
}
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "PaddleOCR Gateway inference test failed." }
