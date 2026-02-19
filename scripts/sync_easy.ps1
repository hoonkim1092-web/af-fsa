param(
    [ValidateSet("up", "down")]
    [string]$Action = "up",

    [ValidateSet("git", "db")]
    [string]$Backend = "git",

    [string]$Target = "all",
    [string]$Agent = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function To-Mode([string]$a) {
    if ($a -eq "up") { return "push" }
    return "pull"
}

function To-ProjectArgs([string]$t) {
    $key = ($t ?? "").Trim().ToLower()
    switch ($key) {
        "all" { return @{ IsMulti = $true; Value = "agent-mind-v22,agent-factory" } }
        default { return @{ IsMulti = $false; Value = $t } }
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$mode = To-Mode $Action
$proj = To-ProjectArgs $Target

if ($Backend -eq "git") {
    $gitScript = Join-Path $PSScriptRoot "project_context_git_sync.ps1"
    if ($proj.IsMulti) {
        if ($Agent) {
            & powershell -ExecutionPolicy Bypass -File $gitScript -Mode $mode -Projects $proj.Value -Agent $Agent
        } else {
            & powershell -ExecutionPolicy Bypass -File $gitScript -Mode $mode -Projects $proj.Value
        }
    } else {
        if ($Agent) {
            & powershell -ExecutionPolicy Bypass -File $gitScript -Mode $mode -Project $proj.Value -Agent $Agent
        } else {
            & powershell -ExecutionPolicy Bypass -File $gitScript -Mode $mode -Project $proj.Value
        }
    }
    exit $LASTEXITCODE
}

$dbScript = Join-Path $PSScriptRoot "project_context_sync.py"
Set-Location $repoRoot
if ($proj.IsMulti) {
    if ($Agent) {
        & python $dbScript --projects $proj.Value --mode $mode --agent $Agent
    } else {
        & python $dbScript --projects $proj.Value --mode $mode
    }
} else {
    if ($Agent) {
        & python $dbScript --project $proj.Value --mode $mode --agent $Agent
    } else {
        & python $dbScript --project $proj.Value --mode $mode
    }
}
exit $LASTEXITCODE
