<#
.SYNOPSIS
Agent Factory CLI v1.2.15 설치 스크립트

.EXAMPLE
irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.15/install-af.ps1 | iex
#>

param(
    [string]$InstallPath = "C:\tools"
)

# 관리자 권한 없으면 자동으로 관리자로 재실행
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Start-Process powershell -ArgumentList "-NoExit -ExecutionPolicy Bypass -Command `"irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.15/install-af.ps1 | iex`"" -Verb RunAs
    exit
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Agent Factory CLI v1.2.15 설치" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Step 1: 기존 설치 탐지 및 제거
Write-Host "`n► 기존 설치 확인 중..." -ForegroundColor Yellow

# PATH에서 af.exe 위치 모두 탐색
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

# 알려진 구버전 설치 경로도 추가 확인
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
    Write-Host "  발견된 기존 설치:" -ForegroundColor Yellow
    foreach ($p in $oldPaths) { Write-Host "    - $p" -ForegroundColor Gray }

    foreach ($p in $oldPaths) {
        try {
            Remove-Item -Path $p -Recurse -Force
            Write-Host "✓ 제거 완료: $p" -ForegroundColor Green
        } catch {
            Write-Host "✗ 제거 실패: $p ($_)" -ForegroundColor Red
        }
    }

    # PATH에서 구버전 경로 제거
    foreach ($scope in @("User", "Machine")) {
        $currentPath = [Environment]::GetEnvironmentVariable("Path", $scope)
        if (-not $currentPath) { continue }
        $newPath = ($currentPath -split ";" | Where-Object { $oldPaths -notcontains $_.TrimEnd("\") }) -join ";"
        if ($newPath -ne $currentPath) {
            [Environment]::SetEnvironmentVariable("Path", $newPath, $scope)
            Write-Host "✓ PATH 정리 완료 ($scope)" -ForegroundColor Green
        }
    }
} else {
    Write-Host "✓ 기존 설치 없음" -ForegroundColor Green
}

# Step 2: 설치 폴더 생성
Write-Host "`n► 설치 폴더 생성 ($InstallPath\af)" -ForegroundColor Yellow
try {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Host "✓ 폴더 생성 완료" -ForegroundColor Green
} catch {
    Write-Host "✗ 폴더 생성 실패: $_" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}

# Step 3: 다운로드
$zipFile = "$InstallPath\af-1.2.15.zip"
Write-Host "`n► af-1.2.15.zip 다운로드 중..." -ForegroundColor Yellow
try {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest `
        -Uri "https://github.com/hoonkim1092-web/af-fsa/raw/af-fsa_v1.2.15/dist/af-1.2.15.zip" `
        -OutFile $zipFile `
        -UseBasicParsing
    $ProgressPreference = 'Continue'

    $sizeMB = [math]::Round((Get-Item $zipFile).Length / 1MB, 1)
    Write-Host "✓ 다운로드 완료 ($sizeMB MB)" -ForegroundColor Green
} catch {
    Write-Host "✗ 다운로드 실패: $_" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}

# Step 4: 압축 해제
Write-Host "`n► 압축 해제 중..." -ForegroundColor Yellow
try {
    Expand-Archive -Path $zipFile -DestinationPath $InstallPath -Force
    Remove-Item $zipFile -Force
    Write-Host "✓ 압축 해제 완료" -ForegroundColor Green
} catch {
    Write-Host "✗ 압축 해제 실패: $_" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}

# Step 5: 설치 확인
$exePath = "$InstallPath\af\af.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "✗ af.exe를 찾을 수 없습니다" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}
Write-Host "✓ af.exe 확인" -ForegroundColor Green

# Step 6: PATH 등록
Write-Host "`n► PATH 등록 중..." -ForegroundColor Yellow
$afPath = "$InstallPath\af"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$afPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$afPath", "User")
    Write-Host "✓ PATH 등록 완료" -ForegroundColor Green
} else {
    Write-Host "✓ 이미 PATH에 등록됨" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  설치 완료! v1.2.15" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PowerShell을 재시작한 후:" -ForegroundColor White
Write-Host "  af --help" -ForegroundColor Yellow
Write-Host ""
Write-Host "  또는 즉시 실행:"
Write-Host "  & '$exePath' --help" -ForegroundColor Yellow
Write-Host ""
Read-Host "엔터를 눌러 종료"
