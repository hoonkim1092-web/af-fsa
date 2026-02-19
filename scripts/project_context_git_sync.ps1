param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("push", "pull")]
    [string]$Mode,

    [string]$Project = "",
    [string]$Projects = "",
    [string]$Agent = "",
    [string]$Branch = "",
    [string]$Message = "",
    [switch]$NoRebase
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Safe-Id([string]$Text) {
    $t = ($Text ?? "").Trim().ToLower()
    $t = [regex]::Replace($t, "[^a-z0-9_-]+", "_")
    $t = [regex]::Replace($t, "_+", "_").Trim("_")
    return $t
}

function Normalize-Key([string]$Text) {
    return [regex]::Replace((($Text ?? "").ToLower()), "[^a-z0-9]+", "")
}

function Parse-ProjectInputs([string]$Single, [string]$Multi) {
    $items = @()
    if ($Single) { $items += $Single }
    if ($Multi) {
        $items += ($Multi -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    $items = $items | Where-Object { $_ } | Select-Object -Unique
    if ($items.Count -eq 0) {
        throw "Provide -Project or -Projects (comma separated)."
    }
    return $items
}

function Resolve-ProjectDir([string]$RepoRoot, [string]$ProjectInput) {
    $projectsRoot = Join-Path $RepoRoot "projects"
    if (-not (Test-Path $projectsRoot)) {
        throw "projects directory not found: $projectsRoot"
    }

    $raw = ($ProjectInput ?? "").Trim()
    if (-not $raw) { throw "Project name is empty." }

    $safe = Safe-Id $raw
    $candidates = @()
    $candidates += $raw
    if ($safe) { $candidates += $safe }

    foreach ($c in $candidates) {
        $p = Join-Path $projectsRoot $c
        if (Test-Path $p) { return $c }
    }

    $targetKey = Normalize-Key $raw
    $matches = @()
    Get-ChildItem -Path $projectsRoot -Directory | ForEach-Object {
        if ((Normalize-Key $_.Name) -eq $targetKey) {
            $matches += $_.Name
        }
    }
    if ($matches.Count -eq 1) {
        return $matches[0]
    }

    # If nothing matched, create canonical directory.
    $created = Join-Path $projectsRoot $safe
    New-Item -ItemType Directory -Path $created -Force | Out-Null
    return $safe
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
git rev-parse --is-inside-work-tree | Out-Null

$projectInputs = Parse-ProjectInputs -Single $Project -Multi $Projects
$projectDirs = @()
foreach ($p in $projectInputs) {
    $projectDirs += (Resolve-ProjectDir -RepoRoot $repoRoot -ProjectInput $p)
}
$projectDirs = $projectDirs | Select-Object -Unique

if ($Mode -eq "pull") {
    if ($Branch) {
        if ($NoRebase) {
            git pull origin $Branch
        } else {
            git pull --rebase origin $Branch
        }
    } else {
        if ($NoRebase) {
            git pull
        } else {
            git pull --rebase
        }
    }
    Write-Host ("OK: pull completed for projects: " + ($projectDirs -join ", "))
    exit 0
}

$agentId = Safe-Id $Agent
$paths = @()
foreach ($dir in $projectDirs) {
    $projectRel = ("projects/" + $dir).Replace("\", "/")
    if ($agentId) {
        $paths += "$projectRel/data/memory/$agentId"
        $paths += "$projectRel/agents/$agentId.yaml"
        $paths += "$projectRel/runs"
        $paths += "$projectRel/artifacts"
        $paths += "$projectRel/dashboard.json"
        $paths += "$projectRel/policies.yaml"
        $paths += "$projectRel/settings.yaml"
        $paths += "$projectRel/skill-lock.yaml"
        $paths += "$projectRel/workflow.yaml"
        $paths += "$projectRel/context_schema.yaml"
    } else {
        $paths += "$projectRel"
    }
}

git add -- $paths

git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host ("No staged changes for projects: " + ($projectDirs -join ", "))
    exit 0
}

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if (-not $Message) {
    if ($agentId) {
        $Message = "sync($($projectDirs -join '+')/$agentId): context snapshot $ts"
    } else {
        $Message = "sync($($projectDirs -join '+')): context snapshot $ts"
    }
}

git commit -m $Message
if ($Branch) {
    git push origin $Branch
} else {
    git push
}

Write-Host ("OK: push completed for projects: " + ($projectDirs -join ", "))
