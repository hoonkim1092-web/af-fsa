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
        "all" { return @{ IsMulti = $true; Value = "logi-mind-v22,agent-factory,@repo" } }
        default {
            $items = @($raw -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
            if (@($items).Count -gt 1) {
                return @{ IsMulti = $true; Value = ($items -join ",") }
            }
            if (@($items).Count -eq 1) {
                return @{ IsMulti = $false; Value = $items[0] }
            }
            return @{ IsMulti = $true; Value = "logi-mind-v22,agent-factory,@repo" }
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
$resumeScript = Join-Path $PSScriptRoot "write_resume_brief.py"
Set-Location $repoRoot
if ($mode -eq "push") {
    if ($proj.IsMulti) {
        & python $resumeScript --projects $proj.Value --trigger sync_push
    } else {
        & python $resumeScript --project $proj.Value --trigger sync_push
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
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
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$globalUserKey = ""
if ($env:AGENT_GLOBAL_USER_KEY) {
    $globalUserKey = $env:AGENT_GLOBAL_USER_KEY.Trim()
}
if (-not $globalUserKey) {
    $envFile = Join-Path $repoRoot ".env"
    if (Test-Path $envFile) {
        $line = Get-Content -Path $envFile -Encoding utf8 | Where-Object { $_ -match '^\s*AGENT_GLOBAL_USER_KEY\s*=' } | Select-Object -First 1
        if ($line) {
            $parts = $line -split "=", 2
            if ($parts.Length -eq 2) {
                $globalUserKey = $parts[1].Trim().Trim('"').Trim("'")
            }
        }
    }
}

if ($globalUserKey) {
    Write-Host "[SYNC GLOBAL] syncing global profile for user_key=$globalUserKey (mode=$mode)..."
    & python $dbScript --mode $mode --scope global --user-key $globalUserKey
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

exit 0
