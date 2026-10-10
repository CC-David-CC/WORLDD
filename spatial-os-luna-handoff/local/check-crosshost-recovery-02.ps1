$ErrorActionPreference = 'Stop'
# Debug crash-forward is already terminal and recorded. Do not repeat it.
$crossChecks = @(
    @('debug', 'reverse', 'crash'),
    @('debug', 'forward', 'delay'),
    @('debug', 'reverse', 'delay'),
    @('runtime-sanitize', 'forward', 'crash'),
    @('runtime-sanitize', 'reverse', 'crash'),
    @('runtime-sanitize', 'forward', 'delay'),
    @('runtime-sanitize', 'reverse', 'delay')
)
foreach ($crossCheck in $crossChecks) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'coordinate-crosshost-check.ps1') `
        -Profile $crossCheck[0] -Direction $crossCheck[1] -Scenario $crossCheck[2] -Candidate recovery -SourceTag 02
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
