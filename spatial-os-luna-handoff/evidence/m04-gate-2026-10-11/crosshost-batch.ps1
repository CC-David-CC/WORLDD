$ErrorActionPreference = 'Stop'
$batchRoot = Join-Path $PSScriptRoot '../../local/m04-crosshost-batch-01'
if (Test-Path -LiteralPath $batchRoot) { throw 'Batch output already exists; inspect the existing run' }
[void][System.IO.Directory]::CreateDirectory($batchRoot)
$batchState = @{ status = 'running'; pid = $PID; candidate = 'f74c6b579c6149f41e1cbd17b025eb92fd453009'; cases = @(); phase_complete = $false }
function Save-Batch {
    $batchState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $batchRoot 'state.json') -Encoding UTF8
}
Save-Batch
try {
    foreach ($crossProfile in @('Debug', 'ASan-UBSan')) {
        foreach ($scenario in @('delay', 'crash')) {
            foreach ($direction in @('forward', 'reverse')) {
                $label = "$crossProfile-$scenario-$direction"
                $batchState.current = $label
                Save-Batch
                & (Join-Path $PSScriptRoot 'coordinate-crosshost.ps1') -CrossProfile $crossProfile -Scenario $scenario -Direction $direction *> (Join-Path $batchRoot "$label.log")
                $batchState.cases += @{ case = $label; status = 'commands_passed_needs_review' }
                Save-Batch
                Write-Output "$label completed"
            }
        }
    }
    $batchState.status = 'commands_passed_needs_review'
    Save-Batch
} catch {
    $batchState.status = 'failed_or_observation_incomplete'
    $batchState.error = $_.Exception.Message
    Save-Batch
    throw
}
