[CmdletBinding()]
param(
    [ValidateSet("firered", "qwen", "whisper", "all")]
    [string]$Provider = "all",
    [string]$ModelRoot = "D:\Models\Video2Doc",
    [string]$EnvironmentRoot = "",
    [string]$Distribution = "Ubuntu",
    [string]$FireRedRevision = "4e7d9aaf4482a47cec1724807026b9b151926eb5"
)

$ErrorActionPreference = "Stop"
$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$requirementsRoot = (& wsl.exe -d $Distribution -- wslpath -a ($runtimeRoot -replace "\\", "/")).Trim()
$wslModelRoot = (& wsl.exe -d $Distribution -- wslpath -a ($ModelRoot -replace "\\", "/")).Trim()
if (-not $wslModelRoot) { throw "Unable to map ModelRoot into WSL." }
$wslEnvironmentRoot = if ([string]::IsNullOrWhiteSpace($EnvironmentRoot)) {
    (& wsl.exe -d $Distribution -- bash -lc 'printf %s "${XDG_DATA_HOME:-$HOME/.local/share}/video2doc-model-runtime"').Trim()
} elseif ($EnvironmentRoot -match '^[A-Za-z]:\\') {
    (& wsl.exe -d $Distribution -- wslpath -a ($EnvironmentRoot -replace "\\", "/")).Trim()
} else {
    $EnvironmentRoot.TrimEnd('/')
}
if (-not $wslEnvironmentRoot) { throw "Unable to resolve EnvironmentRoot in WSL." }

& wsl.exe -d $Distribution -- bash -lc "mkdir -p '$wslModelRoot/logs' '$wslEnvironmentRoot/envs' '$wslEnvironmentRoot/tools/uv' '$wslEnvironmentRoot/tools/python-bin' '$wslEnvironmentRoot/cache/uv' '$wslEnvironmentRoot/python' && if [ ! -x '$wslEnvironmentRoot/tools/uv/uv' ]; then curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/0.11.30/install.sh | env UV_INSTALL_DIR='$wslEnvironmentRoot/tools/uv' UV_NO_MODIFY_PATH=1 sh; fi"
if ($LASTEXITCODE -ne 0) { throw "Unable to create model runtime directories." }

$targets = if ($Provider -eq "all") { @("firered", "qwen", "whisper") } else { @($Provider) }
$pythonVersions = @{ firered = "3.11"; qwen = "3.12"; whisper = "3.12" }
foreach ($target in $targets) {
    $envPath = "$wslEnvironmentRoot/envs/$target"
    $pythonVersion = $pythonVersions[$target]
    & wsl.exe -d $Distribution -- bash -lc "export UV_CACHE_DIR='$wslEnvironmentRoot/cache/uv' UV_PYTHON_INSTALL_DIR='$wslEnvironmentRoot/python' UV_PYTHON_BIN_DIR='$wslEnvironmentRoot/tools/python-bin'; '$wslEnvironmentRoot/tools/uv/uv' python install '$pythonVersion' && '$wslEnvironmentRoot/tools/uv/uv' venv --clear --python '$pythonVersion' '$envPath' && '$wslEnvironmentRoot/tools/uv/uv' pip install --python '$envPath/bin/python' -r '$requirementsRoot/requirements-$target.txt'"
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the $target environment." }
    if ($target -eq "firered") {
        $sourcePath = "$wslModelRoot/firered/FireRedASR2S"
        & wsl.exe -d $Distribution -- bash -lc "mkdir -p '$wslModelRoot/firered' && if [ ! -d '$sourcePath/.git' ]; then git clone --filter=blob:none https://github.com/FireRedTeam/FireRedASR2S.git '$sourcePath'; fi && if ! git -C '$sourcePath' diff --quiet || ! git -C '$sourcePath' diff --cached --quiet || git -C '$sourcePath' ls-files --others --exclude-standard | grep -v '^fireredasr2s\.egg-info/' | grep -q .; then echo 'FireRed source checkout is dirty; refusing to overwrite it.' >&2; exit 2; fi && git -C '$sourcePath' fetch --depth 1 origin '$FireRedRevision' && git -C '$sourcePath' checkout --detach '$FireRedRevision' && export UV_CACHE_DIR='$wslEnvironmentRoot/cache/uv'; '$wslEnvironmentRoot/tools/uv/uv' pip install --python '$envPath/bin/python' '$sourcePath'"
        if ($LASTEXITCODE -ne 0) { throw "Failed to install the official FireRedASR2S repository." }
    }
    Write-Host "[OK] $target environment: $envPath (weights: $wslModelRoot)"
}
