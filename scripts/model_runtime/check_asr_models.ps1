[CmdletBinding()]
param()

$gateways = @(
    @{ Name = "FireRed"; Url = "http://127.0.0.1:8101" },
    @{ Name = "Qwen"; Url = "http://127.0.0.1:8102" },
    @{ Name = "Whisper"; Url = "http://127.0.0.1:8103" }
)
foreach ($gateway in $gateways) {
    try {
        $health = Invoke-RestMethod -Uri ($gateway.Url + "/health") -TimeoutSec 3
        $models = Invoke-RestMethod -Uri ($gateway.Url + "/v1/models") -TimeoutSec 3
        Write-Host "[OK] $($gateway.Name): loaded=$($health.loaded_model), models=$($models.data.id -join ', ')"
    } catch {
        Write-Warning "$($gateway.Name): unavailable at $($gateway.Url)"
    }
}
