[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $repoRoot 'tools\media'
New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest'
$exeAsset = $release.assets | Where-Object name -eq 'yt-dlp.exe'
$sumAsset = $release.assets | Where-Object name -eq 'SHA2-256SUMS'
if (-not $exeAsset -or -not $sumAsset) { throw 'Official yt-dlp release assets are missing.' }
$temporary = Join-Path $toolRoot 'yt-dlp.exe.download'
$checksums = Join-Path $toolRoot 'SHA2-256SUMS'
Invoke-WebRequest -Uri $exeAsset.browser_download_url -OutFile $temporary
Invoke-WebRequest -Uri $sumAsset.browser_download_url -OutFile $checksums
$line = @(Get-Content -LiteralPath $checksums | Where-Object { $_ -match '\s+yt-dlp.exe$' })
if ($line.Count -ne 1) { throw 'No unique checksum for yt-dlp.exe.' }
$expected = ($line[0] -split '\s+')[0]
$actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
if ($actual -ine $expected) { throw 'Official yt-dlp SHA-256 verification failed.' }
Move-Item -LiteralPath $temporary -Destination (Join-Path $toolRoot 'yt-dlp.exe') -Force
Write-Host "Bundled yt-dlp $($release.tag_name), SHA256 $actual"
