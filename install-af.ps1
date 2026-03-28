<#
.SYNOPSIS
Agent Factory CLI v1.2.0 설치 스크립트

.EXAMPLE
irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.0/install-af.ps1 | iex
#>

param(
    [string]$InstallPath = "C:\tools"
)

# 관리자 권한 없으면 자동으로 관리자로 재실행
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Start-Process powershell -ArgumentList "-NoExit -ExecutionPolicy Bypass -Command `"irm https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.2.0/install-af.ps1 | iex`"" -Verb RunAs
    exit
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Agent Factory CLI v1.2.0 설치" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Step 1: 폴더 생성
Write-Host "`n► 설치 폴더 생성 ($InstallPath)" -ForegroundColor Yellow
try {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Host "✓ 폴더 생성 완료" -ForegroundColor Green
} catch {
    Write-Host "✗ 폴더 생성 실패: $_" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}

# Step 2: 다운로드
$zipFile = "$InstallPath\af-1.2.0.zip"
Write-Host "`n► af-1.2.0.zip 다운로드 중..." -ForegroundColor Yellow
try {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest `
        -Uri "https://github.com/hoonkim1092-web/af-fsa/raw/af-fsa_v1.2.0/dist/af-1.2.0.zip" `
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

# Step 3: 압축 해제
Write-Host "`n► 압축 해제 중..." -ForegroundColor Yellow
try {
    Expand-Archive -Path $zipFile -DestinationPath $InstallPath -Force
    Write-Host "✓ 압축 해제 완료" -ForegroundColor Green
} catch {
    Write-Host "✗ 압축 해제 실패: $_" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}

# Step 4: 설치 확인
$exePath = "$InstallPath\af\af.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "✗ af.exe를 찾을 수 없습니다" -ForegroundColor Red
    Read-Host "엔터를 눌러 종료"
    exit 1
}
Write-Host "✓ af.exe 확인" -ForegroundColor Green

# Step 5: PATH 등록
Write-Host "`n► PATH 등록 중..." -ForegroundColor Yellow
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$afPath = "$InstallPath\af"
if ($currentPath -notlike "*$afPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$afPath", "User")
    Write-Host "✓ PATH 등록 완료" -ForegroundColor Green
} else {
    Write-Host "✓ 이미 PATH에 등록됨" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  설치 완료! v1.2.0" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PowerShell을 재시작한 후:" -ForegroundColor White
Write-Host "  af.exe" -ForegroundColor Yellow
Write-Host ""
Write-Host "  또는 즉시 실행:"
Write-Host "  & '$exePath'" -ForegroundColor Yellow
Write-Host ""
Read-Host "엔터를 눌러 종료"
