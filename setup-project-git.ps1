# Run from a normal PowerShell session as the workspace owner.
# This configures only this repository and pushes only to box-git.
$ErrorActionPreference = 'Stop'
$taskRepoRoot = $PSScriptRoot
$taskOrigin = 'dflanag3@box-git:/srv/data/git/spacial_desktop.git'
Push-Location -LiteralPath $taskRepoRoot
try {
    function Invoke-ProjectGit {
        & git @args
        if ($LASTEXITCODE -ne 0) { throw 'Git command failed; setup stopped.' }
    }

    Invoke-ProjectGit rev-parse --show-toplevel
    Invoke-ProjectGit config --local core.autocrlf false
    Invoke-ProjectGit config --local user.name David
    Invoke-ProjectGit config --local user.email david@code.contractors
    Invoke-ProjectGit config --local core.sshCommand 'ssh -o BatchMode=yes -o ConnectTimeout=15'

    $taskRemotes = @(Invoke-ProjectGit remote)
    if ($taskRemotes -contains 'origin') {
        $taskExistingOrigin = Invoke-ProjectGit remote get-url origin
        if ($taskExistingOrigin -ne $taskOrigin) {
            throw 'Existing origin differs from the intended box-git repository.'
        }
    } else {
        Invoke-ProjectGit remote add origin $taskOrigin
    }
    Invoke-ProjectGit ls-remote origin

    & git rev-parse --verify --quiet HEAD
    $taskHasCommit = $LASTEXITCODE -eq 0
    if (-not $taskHasCommit) {
        Invoke-ProjectGit add --all
        Invoke-ProjectGit diff --cached --shortstat
        Invoke-ProjectGit commit -m 'Import spatial desktop project handoffs and shared instructions'
    }
    Invoke-ProjectGit push --set-upstream origin main
    $taskLocalCommit = Invoke-ProjectGit rev-parse HEAD
    $taskRemoteMain = Invoke-ProjectGit ls-remote origin refs/heads/main
    if (($taskRemoteMain -split '\s+')[0] -ne $taskLocalCommit) {
        throw 'Local HEAD and remote main do not match.'
    }
    Invoke-ProjectGit status --short --branch
    Invoke-ProjectGit remote -v
} finally {
    Pop-Location
}
