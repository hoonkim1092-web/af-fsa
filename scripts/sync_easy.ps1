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
    $raw = ""
    if ($null -ne $t) { $raw = $t.Trim() }
    if (-not $raw) { $raw = "all" }

    $key = $raw.ToLower()
    switch ($key) {
        "all" { return @{ IsMulti = $true; Value = "logi-mind-v22,agent-factory" } }
        default {
            $items = @($raw -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
            if (@($items).Count -gt 1) {
                return @{ IsMulti = $true; Value = ($items -join ",") }
            }
            if (@($items).Count -eq 1) {
                return @{ IsMulti = $false; Value = $items[0] }
            }
            return @{ IsMulti = $true; Value = "logi-mind-v22,agent-factory" }
        }
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
