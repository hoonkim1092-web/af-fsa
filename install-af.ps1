<#
.SYNOPSIS
Agent Factory CLI v1.2.13 install script

.EXAMPLE
irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.13/install-af.ps1 | iex
#>

param(
    [string]$InstallPath = "C:\tools"
)

$Version = "1.2.13"
$Branch = "af-fsa_v1.2.13"
$RawBase = "https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/$Branch"
$ScriptUrl = "$RawBase/install-af.ps1"
$ZipName = "af-$Version.zip"
$ZipUrl = "$RawBase/dist/$ZipName"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole] "Administrator"
)

if (-not $isAdmin) {
    $escapedInstallPath = $InstallPath.Replace("'", "''")
    $relaunchCommand = "& ([ScriptBlock]::Create((irm '$ScriptUrl'))) -InstallPath '$escapedInstallPath'"
    Start-Process powershell -ArgumentList "-NoExit -ExecutionPolicy Bypass -Command $relaunchCommand" -Verb RunAs
    exit
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Agent Factory CLI v$Version install" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Write-Host "`nChecking for previous installs..." -ForegroundColor Yellow

$targetDir = Join-Path $InstallPath "af"
$oldPaths = @()
$envPaths = (
    $env:Path + ";" +
    [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
    [Environment]::GetEnvironmentVariable("Path", "Machine")
) -split ";" | Select-Object -Unique

foreach ($p in $envPaths) {
    if ([string]::IsNullOrWhiteSpace($p)) {
        continue
    }
    $candidate = Join-Path $p "af.exe"
    if (Test-Path $candidate) {
        $dir = Split-Path $candidate -Parent
        if ($dir -ne $targetDir) {
            $oldPaths += $dir
        }
    }
}

$knownOldPaths = @(
    "$env:LOCALAPPDATA\AgentFactory",
    "$env:LOCALAPPDATA\af",
    "C:\tools\af",
    "C:\af"
)

foreach ($p in $knownOldPaths) {
    if ((Test-Path "$p\af.exe") -and ($p -ne $targetDir) -and ($oldPaths -notcontains $p)) {
        $oldPaths += $p
    }
}

if ($oldPaths.Count -gt 0) {
    Write-Host "Found previous installs:" -ForegroundColor Yellow
    foreach ($p in $oldPaths) {
        Write-Host "  - $p" -ForegroundColor Gray
    }

    foreach ($p in $oldPaths) {
        try {
            Remove-Item -Path $p -Recurse -Force
            Write-Host "Removed: $p" -ForegroundColor Green
        } catch {
            Write-Host "Failed to remove: $p ($_)" -ForegroundColor Red
        }
    }

    foreach ($scope in @("User", "Machine")) {
        $currentPath = [Environment]::GetEnvironmentVariable("Path", $scope)
        if (-not $currentPath) {
            continue
        }
        $newPath = ($currentPath -split ";" | Where-Object { $oldPaths -notcontains $_.TrimEnd("\") }) -join ";"
        if ($newPath -ne $currentPath) {
            [Environment]::SetEnvironmentVariable("Path", $newPath, $scope)
            Write-Host "PATH cleaned ($scope)" -ForegroundColor Green
        }
    }
} else {
    Write-Host "No previous install found" -ForegroundColor Green
}

Write-Host "`nCreating install directory ($targetDir)..." -ForegroundColor Yellow
try {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Host "Install directory ready" -ForegroundColor Green
} catch {
    Write-Host "Failed to create install directory: $_" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$zipFile = Join-Path $InstallPath $ZipName
Write-Host "`nDownloading $ZipName ..." -ForegroundColor Yellow
try {
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zipFile -UseBasicParsing
    $ProgressPreference = "Continue"

    $sizeMB = [math]::Round((Get-Item $zipFile).Length / 1MB, 1)
    Write-Host "Download complete ($sizeMB MB)" -ForegroundColor Green
} catch {
    Write-Host "Download failed: $_" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "`nExtracting archive..." -ForegroundColor Yellow
try {
    Expand-Archive -Path $zipFile -DestinationPath $InstallPath -Force
    Remove-Item $zipFile -Force
    Write-Host "Extract complete" -ForegroundColor Green
} catch {
    Write-Host "Extract failed: $_" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$exePath = Join-Path $targetDir "af.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "af.exe not found after install" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "af.exe verified" -ForegroundColor Green

Write-Host "`nUpdating user PATH..." -ForegroundColor Yellow
$afPath = $targetDir
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$afPath*") {
    $newUserPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $afPath } else { "$userPath;$afPath" }
    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
    Write-Host "PATH updated" -ForegroundColor Green
} else {
    Write-Host "Already in PATH" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Install complete! v$Version" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Open a new PowerShell window and run:" -ForegroundColor White
Write-Host "  af --help" -ForegroundColor Yellow
Write-Host ""
Write-Host "Or run it immediately:" -ForegroundColor White
Write-Host "  & '$exePath' --help" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to exit"

