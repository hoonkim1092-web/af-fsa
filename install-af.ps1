#!/usr/bin/env powershell
<#
.SYNOPSIS
Agent Factory CLI 설치 스크립트

.DESCRIPTION
GitHub 릴리스에서 af-1.0.2.zip을 다운로드 후 자동으로 압축 해제하고 PATH에 등록합니다.

.PARAMETER InstallPath
설치 경로 (기본값: C:\tools)

.EXAMPLE
.\install.ps1
.\install.ps1 -InstallPath "D:\MyApps"
#>

param(
    [string]$InstallPath = "C:\tools"
)

# 색상 지정
function Write-Header {
    param([string]$Message)
    Write-Host "`n" + ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n► $Message" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Error {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
    exit 1
}

# 관리자 권한 확인
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Error "관리자 권한이 필요합니다. PowerShell을 관리자 모드로 실행해주세요."
}

Write-Header "Agent Factory CLI v1.0.2 설치"

# Step 1: 폴더 생성
Write-Step "설치 폴더 생성 ($InstallPath)"
try {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Success "폴더 생성 완료"
} catch {
    Write-Error "폴더 생성 실패: $_"
}

# Step 2: 파일 다운로드
$zipFile = "$InstallPath\af-1.0.2.zip"
Write-Step "af-1.0.2.zip 다운로드 중..."
try {
    $progressPreference = 'SilentlyContinue'
    Invoke-WebRequest `
        -Uri "https://github.com/hoonkim1092-web/agent-factory/raw/af-fsa_v1.0.2/dist/af-1.0.2.zip" `
        -OutFile $zipFile `
        -UseBasicParsing
    $progressPreference = 'Continue'

    if (Test-Path $zipFile) {
        $sizeMB = [math]::Round((Get-Item $zipFile).Length / 1MB, 1)
        Write-Success "다운로드 완료 ($sizeMB MB)"
    } else {
        Write-Error "다운로드 실패"
    }
} catch {
    Write-Error "다운로드 오류: $_"
}

# Step 3: 압축 해제
Write-Step "압축 해제 중..."
try {
    Expand-Archive -Path $zipFile -DestinationPath $InstallPath -Force
    Write-Success "압축 해제 완료"
} catch {
    Write-Error "압축 해제 실패: $_"
}

# Step 4: 설치 확인
Write-Step "설치 확인"
$exePath = "$InstallPath\af\af.exe"
if (Test-Path $exePath) {
    Write-Success "af.exe 설치 확인"
} else {
    Write-Error "af.exe를 찾을 수 없습니다"
}

# Step 5: PATH 등록
Write-Step "환경변수 PATH에 등록"
try {
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $afPath = "$InstallPath\af"

    if ($currentPath -notlike "*$afPath*") {
        [Environment]::SetEnvironmentVariable(
            "Path",
            "$currentPath;$afPath",
            "User"
        )
        Write-Success "PATH 등록 완료"
    } else {
        Write-Success "이미 PATH에 등록됨"
    }
} catch {
    Write-Error "PATH 등록 실패: $_"
}

# 완료
Write-Header "설치 완료!"
Write-Host "
설치 경로: $exePath
"
Write-Host "다음 단계:" -ForegroundColor Cyan
Write-Host "1. PowerShell을 재시작하세요"
Write-Host "2. 다음 명령어를 실행하세요:"
Write-Host "   af.exe" -ForegroundColor Yellow
Write-Host ""
Write-Host "또는 즉시 실행:"
Write-Host "   & '$exePath'" -ForegroundColor Yellow
Write-Host ""
