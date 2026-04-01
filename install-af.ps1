<#
.SYNOPSIS
Agent Factory CLI v1.2.12 ?ㅼ튂 ?ㅽ겕由쏀듃

.EXAMPLE
irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.12/install-af.ps1 | iex
#>

param(
    [string]$InstallPath = "C:\tools"
)

# 愿由ъ옄 沅뚰븳 ?놁쑝硫??먮룞?쇰줈 愿由ъ옄濡??ъ떎??
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Start-Process powershell -ArgumentList "-NoExit -ExecutionPolicy Bypass -Command `"irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.12/install-af.ps1 | iex`"" -Verb RunAs
    exit
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Agent Factory CLI v1.2.12 ?ㅼ튂" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Step 1: 湲곗〈 ?ㅼ튂 ?먯? 諛??쒓굅
Write-Host "`n??湲곗〈 ?ㅼ튂 ?뺤씤 以?.." -ForegroundColor Yellow

# PATH?먯꽌 af.exe ?꾩튂 紐⑤몢 ?먯깋
$oldPaths = @()
$envPaths = ($env:Path + ";" + [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")) -split ";" | Select-Object -Unique
foreach ($p in $envPaths) {
    $candidate = Join-Path $p "af.exe"
    if (Test-Path $candidate) {
        $dir = Split-Path $candidate -Parent
        if ($dir -ne "$InstallPath\af") {
            $oldPaths += $dir
        }
    }
}

# ?뚮젮吏?援щ쾭???ㅼ튂 寃쎈줈??異붽? ?뺤씤
$knownOldPaths = @(
    "$env:LOCALAPPDATA\AgentFactory",
    "$env:LOCALAPPDATA\af",
    "C:\tools\af",
    "C:\af"
)
foreach ($p in $knownOldPaths) {
    if ((Test-Path "$p\af.exe") -and ($p -ne "$InstallPath\af") -and ($oldPaths -notcontains $p)) {
        $oldPaths += $p
    }
}

if ($oldPaths.Count -gt 0) {
    Write-Host "  諛쒓껄??湲곗〈 ?ㅼ튂:" -ForegroundColor Yellow
    foreach ($p in $oldPaths) { Write-Host "    - $p" -ForegroundColor Gray }

    foreach ($p in $oldPaths) {
        try {
            Remove-Item -Path $p -Recurse -Force
            Write-Host "???쒓굅 ?꾨즺: $p" -ForegroundColor Green
        } catch {
            Write-Host "???쒓굅 ?ㅽ뙣: $p ($_)" -ForegroundColor Red
        }
    }

    # PATH?먯꽌 援щ쾭??寃쎈줈 ?쒓굅
    foreach ($scope in @("User", "Machine")) {
        $currentPath = [Environment]::GetEnvironmentVariable("Path", $scope)
        if (-not $currentPath) { continue }
        $newPath = ($currentPath -split ";" | Where-Object { $oldPaths -notcontains $_.TrimEnd("\") }) -join ";"
        if ($newPath -ne $currentPath) {
            [Environment]::SetEnvironmentVariable("Path", $newPath, $scope)
            Write-Host "??PATH ?뺣━ ?꾨즺 ($scope)" -ForegroundColor Green
        }
    }
} else {
    Write-Host "??湲곗〈 ?ㅼ튂 ?놁쓬" -ForegroundColor Green
}

# Step 2: ?ㅼ튂 ?대뜑 ?앹꽦
Write-Host "`n???ㅼ튂 ?대뜑 ?앹꽦 ($InstallPath\af)" -ForegroundColor Yellow
try {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Host "???대뜑 ?앹꽦 ?꾨즺" -ForegroundColor Green
} catch {
    Write-Host "???대뜑 ?앹꽦 ?ㅽ뙣: $_" -ForegroundColor Red
    Read-Host "?뷀꽣瑜??뚮윭 醫낅즺"
    exit 1
}

# Step 3: ?ㅼ슫濡쒕뱶
$zipFile = "$InstallPath\af-1.2.12.zip"
Write-Host "`n??af-1.2.12.zip ?ㅼ슫濡쒕뱶 以?.." -ForegroundColor Yellow
try {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest `
        -Uri "https://github.com/hoonkim1092-web/af-fsa/raw/af-fsa_v1.2.12/dist/af-1.2.12.zip" `
        -OutFile $zipFile `
        -UseBasicParsing
    $ProgressPreference = 'Continue'

    $sizeMB = [math]::Round((Get-Item $zipFile).Length / 1MB, 1)
    Write-Host "???ㅼ슫濡쒕뱶 ?꾨즺 ($sizeMB MB)" -ForegroundColor Green
} catch {
    Write-Host "???ㅼ슫濡쒕뱶 ?ㅽ뙣: $_" -ForegroundColor Red
    Read-Host "?뷀꽣瑜??뚮윭 醫낅즺"
    exit 1
}

# Step 4: ?뺤텞 ?댁젣
Write-Host "`n???뺤텞 ?댁젣 以?.." -ForegroundColor Yellow
try {
    Expand-Archive -Path $zipFile -DestinationPath $InstallPath -Force
    Remove-Item $zipFile -Force
    Write-Host "???뺤텞 ?댁젣 ?꾨즺" -ForegroundColor Green
} catch {
    Write-Host "???뺤텞 ?댁젣 ?ㅽ뙣: $_" -ForegroundColor Red
    Read-Host "?뷀꽣瑜??뚮윭 醫낅즺"
    exit 1
}

# Step 5: ?ㅼ튂 ?뺤씤
$exePath = "$InstallPath\af\af.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "??af.exe瑜?李얠쓣 ???놁뒿?덈떎" -ForegroundColor Red
    Read-Host "?뷀꽣瑜??뚮윭 醫낅즺"
    exit 1
}
Write-Host "??af.exe ?뺤씤" -ForegroundColor Green

# Step 6: PATH ?깅줉
Write-Host "`n??PATH ?깅줉 以?.." -ForegroundColor Yellow
$afPath = "$InstallPath\af"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$afPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$afPath", "User")
    Write-Host "??PATH ?깅줉 ?꾨즺" -ForegroundColor Green
} else {
    Write-Host "???대? PATH???깅줉?? -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ?ㅼ튂 ?꾨즺! v1.2.12" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PowerShell???ъ떆?묓븳 ??" -ForegroundColor White
Write-Host "  af --help" -ForegroundColor Yellow
Write-Host ""
Write-Host "  ?먮뒗 利됱떆 ?ㅽ뻾:"
Write-Host "  & '$exePath' --help" -ForegroundColor Yellow
Write-Host ""
Read-Host "?뷀꽣瑜??뚮윭 醫낅즺"

