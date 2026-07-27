[CmdletBinding()]
param(
    [ValidateSet("firered", "qwen", "whisper", "all")]
    [string]$Provider = "all",
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$EnvironmentRoot = "",
    [string]$Distribution = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$wslModelRoot = (& wsl.exe -d $Distribution -- wslpath -a ($ModelRoot -replace "\\", "/")).Trim()
$wslEnvironmentRoot = if ([string]::IsNullOrWhiteSpace($EnvironmentRoot)) {
    (& wsl.exe -d $Distribution -- bash -lc 'printf %s "${XDG_DATA_HOME:-$HOME/.local/share}/video2doc-model-runtime"').Trim()
} elseif ($EnvironmentRoot -match '^[A-Za-z]:\\') {
    (& wsl.exe -d $Distribution -- wslpath -a ($EnvironmentRoot -replace "\\", "/")).Trim()
} else {
    $EnvironmentRoot.TrimEnd('/')
}
$targets = if ($Provider -eq "all") { @("firered", "qwen", "whisper") } else { @($Provider) }
$huggingFaceEnvironment = "export HF_HUB_DISABLE_XET='1' HF_HUB_DOWNLOAD_TIMEOUT='600' HF_HUB_ETAG_TIMEOUT='60';"
foreach ($target in $targets) {
    $python = "$wslEnvironmentRoot/envs/$target/bin/python"
    $hf = "$wslEnvironmentRoot/envs/$target/bin/hf"
    $uv = "$wslEnvironmentRoot/tools/uv/uv"
    & wsl.exe -d $Distribution -- bash -lc "export UV_CACHE_DIR='$wslEnvironmentRoot/cache/uv'; '$uv' pip install --python '$python' 'huggingface_hub[cli]'"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install huggingface_hub in $target environment." }
    if ($target -eq "firered") {
        & wsl.exe -d $Distribution -- bash -lc "$huggingFaceEnvironment '$hf' download FireRedTeam/FireRedASR2-AED --local-dir '$wslModelRoot/firered/FireRedASR2-AED'"
    } elseif ($target -eq "qwen") {
        & wsl.exe -d $Distribution -- bash -lc "$huggingFaceEnvironment '$hf' download Qwen/Qwen3-ASR-1.7B --local-dir '$wslModelRoot/qwen/Qwen3-ASR-1.7B' && '$hf' download Qwen/Qwen3-ForcedAligner-0.6B --local-dir '$wslModelRoot/qwen/Qwen3-ForcedAligner-0.6B'"
    } else {
        & wsl.exe -d $Distribution -- bash -lc "$huggingFaceEnvironment '$hf' download Systran/faster-whisper-large-v3 --local-dir '$wslModelRoot/whisper/large-v3' && '$hf' download mobiuslabsgmbh/faster-whisper-large-v3-turbo --local-dir '$wslModelRoot/whisper/large-v3-turbo'"
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to download $target model weights." }
    Write-Host "[OK] Downloaded $target weights under $ModelRoot"
}
