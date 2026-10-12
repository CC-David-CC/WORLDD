$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$repo = (Resolve-Path (Join-Path $root '..')).Path
$historical = 'ee883e7'
$candidate = 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
function Read-GitFile([string]$revision, [string]$path) {
    $lines = & git -C $repo show "${revision}:spatial-os-luna-handoff/$path"
    if ($LASTEXITCODE -ne 0) { throw "Cannot read $revision $path" }
    return ($lines -join "`n")
}
function Hash-File([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$compared = @()
foreach ($spec in @(
    @('server/xlibre/spatialpolicy.c', 'static void', 'device_administration'),
    @('server/xlibre/spatialpolicy.c', 'static void', 'server_access'),
    @('server/xlibre/spatialpolicy.c', 'static void', 'screensaver_access'),
    @('server/xlibre/spatialsecurity.c', 'Bool', 'SpatialSecurityPrivileged'),
    @('server/xlibre/spatialsecurity.c', 'Bool', 'SpatialSecuritySameApplication')
)) {
    $pattern = '(?ms)^' + [regex]::Escape($spec[1] + ' ' + $spec[2]) + '\(.*?^\}'
    $before = [regex]::Match((Read-GitFile $historical $spec[0]), $pattern).Value
    $after = [regex]::Match((Read-GitFile $candidate $spec[0]), $pattern).Value
    if (!$before.Length -or $before -cne $after) { throw "Changed function: $($spec[2])" }
    $compared += $spec[2]
}
$guardPatch = 'server/patches/0006-global-server-access.patch'
if ((Read-GitFile $historical $guardPatch) -cne (Read-GitFile $candidate $guardPatch)) {
    throw 'Global administration DIX guards changed'
}
$results = @()
foreach ($spec in @(
    @('native-device-administration-2026-10-08', 'test_device_administration.py', 6),
    @('native-global-administration-2026-10-08', 'test_global_isolation.py', 5)
)) {
    foreach ($node in @('sb16', 'sb16-2')) {
        $directory = Join-Path $root "evidence/$($spec[0])/$node/mutations"
        $receipt = Get-Content (Join-Path $directory 'run.json') -Raw | ConvertFrom-Json
        if ($receipt.variants.Count -ne $spec[2]) { throw 'Missing mutation variants' }
        if ((Hash-File (Join-Path $root "tests/native/$($spec[1])")) -ne $receipt.test_sha256) {
            throw "Witness changed: $($spec[1])"
        }
        foreach ($variant in $receipt.variants) {
            $logPath = Join-Path $directory "$($variant.name)/witness.log"
            $log = Get-Content $logPath -Raw
            $failures = @([regex]::Matches($log, '(?m)^FAIL: (\S+)') | ForEach-Object { $_.Groups[1].Value })
            if ($variant.status -ne 'detected' -or $variant.exit_code -ne 1 -or
                $failures.Count -ne 1 -or $failures[0] -ne $variant.target -or
                $log -notmatch 'Ran 1 test' -or $log -match '(?m)^ERROR:' -or
                $log -notmatch 'Expected native BadAccess') {
                throw "Unexpected mutation result: $node $($variant.name)"
            }
            $results += [ordered]@{ host=$node; mutation=$variant.name; witness=$variant.target;
                log_sha256=(Hash-File $logPath); historical_binary_sha256=$variant.binary_sha256 }
        }
    }
}
$review = [ordered]@{
    status='historical_bypass_evidence_reassessed_current_gate_pending'
    candidate=$candidate; historical_source=$historical; phase_complete=$false
    unchanged_functions=$compared; unchanged_guard_patch=$guardPatch; variants=$results
    limits=@('Historical mutations were not rerun on the candidate.',
        'Callback registration gained other callbacks. Current full native integration must pass.',
        'Shared authentication and resource fixtures changed. Current identity and resource suites must pass.',
        'This review covers only device and global administration, not other mutation requirements.')
}
$review | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'administration-mutation-reassessment.json')
Write-Output "Reassessed $($results.Count) historical mutation results; current gate remains pending."
