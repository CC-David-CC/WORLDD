param(
    [Parameter(Mandatory=$true)][ValidateSet('debug', 'runtime-sanitize')][string]$Profile,
    [Parameter(Mandatory=$true)][ValidateSet('forward', 'reverse')][string]$Direction,
    [Parameter(Mandatory=$true)][ValidateSet('delay', 'crash')][string]$Scenario
)
$ErrorActionPreference = 'Stop'
$crossRemoteBase = '/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local'
$crossRun = "$Profile-$Scenario-${Direction}01"
$crossIssuer = if ($Direction -eq 'forward') { 'sb16' } else { 'sb16-2' }
$crossRemoteRun = "$crossRemoteBase/m02-gate-crosshost-02/runs/$crossRun"
$crossLocal = Join-Path $PSScriptRoot "coordinate-m02-gate-02-$crossRun"
if (Test-Path -LiteralPath $crossLocal) { throw "Coordination output already exists: $crossLocal" }
$crossSsh = (Get-Command ssh.exe).Source
$crossScp = (Get-Command scp.exe).Source
$crossNodes = [System.Collections.Generic.List[object]]::new()
$crossPreflights = @()
foreach ($crossMachine in @('sb16', 'sb16-2')) {
    $crossCheck = & $crossSsh -o BatchMode=yes -o ConnectTimeout=10 "dflanag3@$crossMachine" python3 `
        "$crossRemoteBase/run-m02-gate-crosshost-02.py" worker $Profile $Scenario $Direction --check-only
    if ($LASTEXITCODE -ne 0) { throw "Local qualification is not ready on $crossMachine; no cross-host node was started" }
    $crossPreflights += ($crossCheck | ConvertFrom-Json)
}
if (@($crossPreflights.commit | Select-Object -Unique).Count -ne 1) { throw 'Hosts have different frozen source commits' }
[void][System.IO.Directory]::CreateDirectory($crossLocal)
$crossState = @{ status = 'starting'; profile = $Profile; direction = $Direction; candidate = 'm02-phase-gate-02'; scenario = $Scenario; nodes = @(); gate_preflights = $crossPreflights }


function Save-CrossState {
    $crossState.nodes = @($crossNodes | ForEach-Object {
        $_.Process.Refresh()
        @{ machine = $_.Machine; mode = $_.Mode; pid = $_.Process.Id; exited = $_.Process.HasExited }
    })
    $crossState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $crossLocal 'state.json') -Encoding UTF8
}

function Start-CrossNode([string]$Machine, [string]$Mode) {
    $crossArgs = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=30',
        "dflanag3@$Machine", 'python3', "$crossRemoteBase/run-m02-gate-crosshost-02.py", $Mode, $Profile, $Scenario, $Direction)
    $crossOut = Join-Path $crossLocal "$Machine-$Mode.stdout.log"
    $crossErr = Join-Path $crossLocal "$Machine-$Mode.stderr.log"
    $crossProcess = Start-Process -FilePath $crossSsh -ArgumentList $crossArgs -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $crossOut -RedirectStandardError $crossErr
    $null = $crossProcess.Handle
    $crossNodes.Add([pscustomobject]@{ Machine = $Machine; Mode = $Mode; Process = $crossProcess; Output = $crossOut; Error = $crossErr })
    Save-CrossState
    Write-Output "Started $Machine $Mode; local SSH PID $($crossProcess.Id)"
}

try {
    Start-CrossNode $crossIssuer 'controller'
    $crossDeadline = [DateTime]::UtcNow.AddSeconds(80)
    do {
        & $crossSsh -o BatchMode=yes -o ConnectTimeout=5 "dflanag3@$crossIssuer" test -f "$crossRemoteRun/controller/ready.json"
        if ($LASTEXITCODE -eq 0) { break }
        $crossNodes[0].Process.Refresh()
        if ($crossNodes[0].Process.HasExited) { throw 'Controller exited before its readiness file appeared' }
        if ([DateTime]::UtcNow -ge $crossDeadline) { throw 'Controller readiness observation expired; inspect the existing service before another attempt' }
        Start-Sleep -Milliseconds 250
    } while ($true)
    $crossBundle = Join-Path $crossLocal 'private-bundle'
    & $crossScp -q -r "dflanag3@${crossIssuer}:$crossRemoteRun/controller/private-bundle" $crossBundle
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy the private disposable fixture bundle' }
    foreach ($crossMachine in @('sb16', 'sb16-2')) {
        & $crossSsh -o BatchMode=yes "dflanag3@$crossMachine" mkdir -p -m 700 $crossRemoteRun
        if ($LASTEXITCODE -ne 0) { throw "Could not prepare $crossMachine run directory" }
        & $crossScp -q -r $crossBundle "dflanag3@${crossMachine}:$crossRemoteRun/private-bundle"
        if ($LASTEXITCODE -ne 0) { throw "Could not deliver fixture bundle to $crossMachine" }
    }
    Start-CrossNode 'sb16' 'worker'
    Start-CrossNode 'sb16-2' 'worker'
    $crossState.status = 'running'; Save-CrossState
    $crossDeadline = [DateTime]::UtcNow.AddSeconds(290)
    while (@($crossNodes | Where-Object { $_.Process.Refresh(); -not $_.Process.HasExited }).Count) {
        if ([DateTime]::UtcNow -ge $crossDeadline) { throw 'Existing component observation expired; do not start a duplicate run' }
        Start-Sleep -Milliseconds 200
    }
    $crossResults = @()
    foreach ($crossNode in $crossNodes) {
        $crossNode.Process.WaitForExit()
        $crossResults += @{ machine = $crossNode.Machine; mode = $crossNode.Mode; exit_code = $crossNode.Process.ExitCode }
        Get-Content -LiteralPath $crossNode.Output
        Get-Content -LiteralPath $crossNode.Error
    }
    $crossState.results = $crossResults
    if (@($crossResults | Where-Object { $_.exit_code -ne 0 }).Count) { throw 'One or more cross-host components failed' }
    $crossState.status = 'passed'
    Write-Output "Completed $Profile ${Direction}: controller and both workers exited successfully"
} catch {
    $crossState.status = 'failed_or_observation_incomplete'
    $crossState.error = $_.Exception.Message
    throw
} finally {
    Save-CrossState
}
