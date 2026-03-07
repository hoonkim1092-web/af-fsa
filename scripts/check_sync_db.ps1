param(
    [string]$Projects = "logi-mind-v22,agent-factory,@repo",
    [string]$Table = "project_context_sync"
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

function Load-DotEnv([string]$EnvPath) {
    $map = @{}
    if (-not (Test-Path $EnvPath)) { return $map }
    Get-Content $EnvPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
        $idx = $line.IndexOf("=")
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        if ($k) { $map[$k] = $v }
    }
    return $map
}

function Resolve-ProjectPath([string]$RepoRoot, [string]$ProjectInput) {
    $raw = ""
    if ($null -ne $ProjectInput) { $raw = $ProjectInput.Trim() }
    if (-not $raw) { throw "Project input is empty." }

    $safe = Safe-Id $raw
    $targetKey = Normalize-Key $raw
    $projectsRoot = Join-Path $RepoRoot "projects"
    $siblingsRoot = Split-Path -Parent $RepoRoot

    if (Is-RepoRootAlias -RepoRoot $RepoRoot -ProjectInput $raw) {
        return $RepoRoot
    }

    if ([System.IO.Path]::IsPathRooted($raw)) {
        if (Test-Path $raw -PathType Container) {
            return (Resolve-Path $raw).Path
        }
    } else {
        $direct = Join-Path $RepoRoot $raw
        if (Test-Path $direct -PathType Container) {
            return (Resolve-Path $direct).Path
        }
    }

    $candidates = @(
        (Join-Path $projectsRoot $raw),
        (Join-Path $projectsRoot $safe),
        (Join-Path $siblingsRoot $raw),
        (Join-Path $siblingsRoot $safe)
    )
    foreach ($p in $candidates) {
        if (-not (Test-Path $p -PathType Container)) { continue }
        if (Is-SamePath $p $RepoRoot) { continue }
        return (Resolve-Path $p).Path
    }

    foreach ($p in (Get-ChildItem -Path $projectsRoot -Directory -ErrorAction SilentlyContinue)) {
        if ((Normalize-Key $p.Name) -eq $targetKey) { return $p.FullName }
    }
    foreach ($p in (Get-ChildItem -Path $siblingsRoot -Directory -ErrorAction SilentlyContinue)) {
        if (Is-SamePath $p.FullName $RepoRoot) { continue }
        if ((Normalize-Key $p.Name) -eq $targetKey) { return $p.FullName }
    }
    if ($targetKey -and ((Normalize-Key (Split-Path -Leaf $RepoRoot)) -eq $targetKey)) {
        return $RepoRoot
    }
    return ""
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$rootEnv = Load-DotEnv (Join-Path $repoRoot ".env")
$projectItems = $Projects.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }

$results = @()
foreach ($proj in $projectItems) {
    $projectPath = Resolve-ProjectPath -RepoRoot $repoRoot -ProjectInput $proj
    $projEnv = @{}
    if ($projectPath) {
        $projEnv = Load-DotEnv (Join-Path $projectPath ".env")
    }

    $url = ""
    if ($projEnv.ContainsKey("SUPABASE_URL")) { $url = $projEnv["SUPABASE_URL"] }
    elseif ($rootEnv.ContainsKey("SUPABASE_URL")) { $url = $rootEnv["SUPABASE_URL"] }

    $key = ""
    if ($projEnv.ContainsKey("SUPABASE_KEY")) { $key = $projEnv["SUPABASE_KEY"] }
    elseif ($rootEnv.ContainsKey("SUPABASE_KEY")) { $key = $rootEnv["SUPABASE_KEY"] }

    if (-not $url -or -not $key) {
        $results += [pscustomobject]@{
            project = $proj
            project_path = $projectPath
            ok = $false
            status = "env_missing"
            detail = "SUPABASE_URL/SUPABASE_KEY not found"
        }
        continue
    }

    $url = $url.TrimEnd("/")
    $h = @{ apikey = $key; Authorization = "Bearer $key" }
    $endpoint = "$url/rest/v1/${Table}?select=project_id&limit=1"
    try {
        $resp = Invoke-WebRequest -Method Get -Uri $endpoint -Headers $h -ErrorAction Stop
        $results += [pscustomobject]@{
            project = $proj
            project_path = $projectPath
            ok = $true
            status = [int]$resp.StatusCode
            detail = "table accessible"
        }
    } catch {
        $status = "unknown"
        $detail = $_.Exception.Message
        if ($_.Exception.Response) {
            try { $status = [int]$_.Exception.Response.StatusCode } catch {}
            try {
                $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $body = $sr.ReadToEnd()
                if ($body) { $detail = $body }
            } catch {}
        }
        $results += [pscustomobject]@{
            project = $proj
            project_path = $projectPath
            ok = $false
            status = $status
            detail = $detail
        }
    }
}

$okAll = ($results | Where-Object { -not $_.ok }).Count -eq 0
[pscustomobject]@{
    ok = $okAll
    table = $Table
    items = $results
} | ConvertTo-Json -Depth 5
