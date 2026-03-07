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
    $t = ""
    if ($null -ne $Text) { $t = $Text.Trim().ToLower() }
    $t = [regex]::Replace($t, "[^a-z0-9_-]+", "_")
    $t = [regex]::Replace($t, "_+", "_").Trim("_")
    return $t
}

function Normalize-Key([string]$Text) {
    $v = ""
    if ($null -ne $Text) { $v = $Text.ToLower() }
    return [regex]::Replace($v, "[^a-z0-9]+", "")
}

function Is-SamePath([string]$Left, [string]$Right) {
    try {
        return ((Resolve-Path $Left).Path -eq (Resolve-Path $Right).Path)
    } catch {
        return $false
    }
}

function Is-RepoRootAlias([string]$RepoRoot, [string]$ProjectInput) {
    $raw = ""
    if ($null -ne $ProjectInput) { $raw = $ProjectInput.Trim() }
    if (-not $raw) { return $false }

    switch ($raw.ToLower()) {
        "@repo" { return $true }
        "@root" { return $true }
        "." { return $true }
        "./" { return $true }
        ".\" { return $true }
    }

    $candidate = $raw
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path $RepoRoot $candidate
    }
    if (-not (Test-Path $candidate -PathType Container)) {
        return $false
    }
    return (Is-SamePath $candidate $RepoRoot)
}

function Is-GitRepoPath([string]$Path) {
    try {
        $out = git -C $Path rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        return (Is-SamePath "$out".Trim() $Path)
    } catch {
        return $false
    }
}

function Parse-ProjectInputs([string]$Single, [string]$Multi) {
    $items = @()
    if ($Single) { $items += $Single }
    if ($Multi) {
        $items += ($Multi -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    $items = @($items | Where-Object { $_ } | Select-Object -Unique)
    if (@($items).Count -eq 0) {
        throw "Provide -Project or -Projects (comma separated)."
    }
    return $items
}

function Resolve-ProjectPath([string]$RepoRoot, [string]$ProjectInput) {
    $raw = ""
    if ($null -ne $ProjectInput) { $raw = $ProjectInput.Trim() }
    if (-not $raw) { throw "Project name is empty." }

    $safe = Safe-Id $raw
    $key = Normalize-Key $raw
    $repoKey = Normalize-Key (Split-Path -Leaf $RepoRoot)
    $projectsRoot = Join-Path $RepoRoot "projects"
    $siblingsRoot = Split-Path -Parent $RepoRoot
    $preferRepoRootOnCollision = ($key -and ($repoKey -eq $key))

    # 0) Explicit repo-root aliases only.
    if (Is-RepoRootAlias -RepoRoot $RepoRoot -ProjectInput $raw) {
        return $RepoRoot
    }

    # 1) Existing path as-given or relative to the repo root.
    $directCandidates = @()
    if ([System.IO.Path]::IsPathRooted($raw)) {
        $directCandidates += $raw
    } else {
        $directCandidates += (Join-Path $RepoRoot $raw)
    }
    foreach ($p in $directCandidates) {
        if (Test-Path $p -PathType Container) {
            if (Is-SamePath $p $RepoRoot) { return $RepoRoot }
            return (Resolve-Path $p).Path
        }
    }

    # 2) Prefer local projects/ when the name collides with the workspace repo.
    $p1 = Join-Path $projectsRoot $raw
    if (Test-Path $p1 -PathType Container) {
        if ((-not $preferRepoRootOnCollision) -or (Is-GitRepoPath $p1)) {
            return (Resolve-Path $p1).Path
        }
    }
    if ($safe) {
        $p2 = Join-Path $projectsRoot $safe
        if (Test-Path $p2 -PathType Container) {
            if ((-not $preferRepoRootOnCollision) -or (Is-GitRepoPath $p2)) {
                return (Resolve-Path $p2).Path
            }
        }
    }
    foreach ($p in (Get-ChildItem -Path $projectsRoot -Directory -ErrorAction SilentlyContinue)) {
        if ((Normalize-Key $p.Name) -ne $key) { continue }
        if ($preferRepoRootOnCollision -and (-not (Is-GitRepoPath $p.FullName))) { continue }
        return $p.FullName
    }

    # 3) Then allow sibling repos, but never treat the current repo root as a sibling hit.
    $cands = @()
    $cands += (Join-Path $siblingsRoot $raw)
    if ($safe) { $cands += (Join-Path $siblingsRoot $safe) }
    foreach ($p in $cands) {
        if (-not (Test-Path $p -PathType Container)) { continue }
        if (Is-SamePath $p $RepoRoot) { continue }
        if (Test-Path $p -PathType Container) { return (Resolve-Path $p).Path }
    }

    foreach ($p in (Get-ChildItem -Path $siblingsRoot -Directory -ErrorAction SilentlyContinue)) {
        if (Is-SamePath $p.FullName $RepoRoot) { continue }
        if ((Normalize-Key $p.Name) -eq $key) { return $p.FullName }
    }

    # 4) Bare repo-name fallback only after local/sibling project checks.
    if ($key -and ($repoKey -eq $key)) {
        return $RepoRoot
    }

    # 5) Create under projects as last resort.
    $created = Join-Path $projectsRoot $safe
    New-Item -ItemType Directory -Path $created -Force | Out-Null
    return (Resolve-Path $created).Path
}

function Ensure-GitRepo([string]$Path) {
    try {
        $out = git -C $Path rev-parse --is-inside-work-tree 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "true")
    } catch {
        return $false
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$projectInputs = Parse-ProjectInputs -Single $Project -Multi $Projects
$projectPaths = @()
foreach ($p in $projectInputs) {
    $projectPaths += (Resolve-ProjectPath -RepoRoot $repoRoot -ProjectInput $p)
}
$projectPaths = @($projectPaths | Select-Object -Unique)

$agentId = Safe-Id $Agent
$results = @()

foreach ($projectPath in $projectPaths) {
    if (-not (Ensure-GitRepo $projectPath)) {
        $results += [pscustomobject]@{
            project_path = $projectPath
            ok = $false
            mode = $Mode
            detail = "not_git_repo"
        }
        continue
    }

    if ($Mode -eq "pull") {
        if ($Branch) {
            if ($NoRebase) { git -C $projectPath pull origin $Branch } else { git -C $projectPath pull --rebase --autostash origin $Branch }
        } else {
            if ($NoRebase) { git -C $projectPath pull } else { git -C $projectPath pull --rebase --autostash }
        }
        if ($LASTEXITCODE -ne 0) {
            $results += [pscustomobject]@{
                project_path = $projectPath
                ok = $false
                mode = $Mode
                detail = "git_pull_failed"
            }
            continue
        }
        $results += [pscustomobject]@{
            project_path = $projectPath
            ok = $true
            mode = $Mode
            detail = "pulled"
        }
        continue
    }

    # push
    if ($agentId) {
        $paths = @(
            "data/memory/$agentId",
            "agents/$agentId.yaml",
            "runs",
            "artifacts",
            "dashboard.json",
            "policies.yaml",
            "settings.yaml",
            "skill-lock.yaml",
            "workflow.yaml",
            "context_schema.yaml"
        )
        git -C $projectPath add -- $paths
    } else {
        # Keep syncCompyne in DB-only sync scope (do not commit/push it via git sync).
        git -C $projectPath add -A -- . ":(exclude)syncCompyne/**"
    }

    git -C $projectPath diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        $results += [pscustomobject]@{
            project_path = $projectPath
            ok = $true
            mode = $Mode
            detail = "no_changes"
        }
        continue
    }

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $projName = Split-Path -Leaf $projectPath
    $msg = $Message
    if (-not $msg) {
        if ($agentId) { $msg = "sync($projName/$agentId): context snapshot $ts" }
        else { $msg = "sync($projName): context snapshot $ts" }
    }

    git -C $projectPath commit -m $msg
    if ($LASTEXITCODE -ne 0) {
        $results += [pscustomobject]@{
            project_path = $projectPath
            ok = $false
            mode = $Mode
            detail = "git_commit_failed"
        }
        continue
    }

    if ($Branch) { git -C $projectPath push origin $Branch } else { git -C $projectPath push }
    if ($LASTEXITCODE -ne 0) {
        $results += [pscustomobject]@{
            project_path = $projectPath
            ok = $false
            mode = $Mode
            detail = "git_push_failed"
        }
        continue
    }
    $results += [pscustomobject]@{
        project_path = $projectPath
        ok = $true
        mode = $Mode
        detail = "pushed"
    }
}

$failCount = @($results | Where-Object { -not $_.ok }).Count
$okAll = ($failCount -eq 0)
[pscustomobject]@{
    ok = $okAll
    items = $results
} | ConvertTo-Json -Depth 5

if (-not $okAll) { exit 1 }
