$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$repo = (Resolve-Path (Join-Path $root '..')).Path
$historical = '0578b9e'
$candidate = 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
function Read-Source([string]$revision, [string]$path) {
    $lines = & git -C $repo show "${revision}:spatial-os-luna-handoff/$path"
    if ($LASTEXITCODE -ne 0) { throw "Cannot read $revision $path" }
    return ($lines -join "`n")
}
$patch = 'server/patches/0004-spatial-resource-policy.patch'
if ((Read-Source $historical $patch) -cne (Read-Source $candidate $patch)) { throw 'DIX guards changed' }
$functions = @('SpatialSecurityPrivileged', 'SpatialSecuritySameApplication', 'SpatialSecurityVisibleResource')
foreach ($name in $functions) {
    $pattern = '(?ms)^Bool ' + $name + '\(.*?^\}'
    $before = [regex]::Match((Read-Source $historical 'server/xlibre/spatialsecurity.c'), $pattern).Value
    $after = [regex]::Match((Read-Source $candidate 'server/xlibre/spatialsecurity.c'), $pattern).Value
    if (!$before.Length -or $before -cne $after) { throw "Changed predicate: $name" }
}
$directory = Join-Path $root 'evidence/native-resource-access-2026-10-07/sb16/mutation-02'
$receipt = Get-Content (Join-Path $directory 'mutation.json') -Raw | ConvertFrom-Json
$results = @()
foreach ($name in @('root-enumeration', 'parent-relative-pixels')) {
    $row = @($receipt.mutations | Where-Object { $_.name -eq $name })
    if ($row.Count -ne 1 -or $row[0].status -ne 'detected' -or $row[0].exit_code -ne 1) { throw 'Missing detection' }
    $test = $row[0].test
    $pattern = '(?ms)^    def ' + $test + '\(.*?(?=^    def |^if __name__)'
    $before = [regex]::Match((Read-Source $historical 'tests/native/test_resource_isolation.py'), $pattern).Value
    $after = [regex]::Match((Read-Source $candidate 'tests/native/test_resource_isolation.py'), $pattern).Value
    if (!$before.Length -or $before -cne $after) { throw "Changed witness: $test" }
    $logPath = Join-Path $directory "$name-witness.log"
    $log = Get-Content $logPath -Raw
    $failures = @([regex]::Matches($log, '(?m)^FAIL: (\S+)'))
    if ($failures.Count -ne 1 -or $failures[0].Groups[1].Value -ne $test -or
        $log -notmatch 'Ran 1 test' -or $log -match '(?m)^ERROR:') { throw "Unexpected failure: $name" }
    $results += @{ mutation=$name; witness=$test; host='sb16';
        log_sha256=(Get-FileHash $logPath -Algorithm SHA256).Hash.ToLowerInvariant();
        historical_binary_sha256=$row[0].binary_sha256 }
}
$review = [ordered]@{
    status='historical_bypass_evidence_reassessed_current_gate_pending'; phase_complete=$false
    historical_source=$historical; candidate=$candidate; unchanged_patch=$patch
    unchanged_predicates=$functions; variants=$results
    limits=@('These two historical mutation runs are from sb16 only.',
        'Current full resource and identity suites must pass on both hosts.',
        'Other authentication and resource callback changes have separate fresh mutation runs.',
        'This reassessment does not complete M04 or qualify later milestones.')
}
$json = ($review | ConvertTo-Json -Depth 6).Replace("`r`n", "`n") + "`n"
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'resource-guard-reassessment.json'), $json, (New-Object Text.UTF8Encoding $false))
Write-Output 'Two historical DIX guard mutations reassessed; current gate remains pending.'
