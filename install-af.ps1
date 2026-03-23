#Requires -Version 5.1
<#
.SYNOPSIS
    Agent Factory (af) 설치 스크립트

.DESCRIPTION
    af.exe를 다운로드하고 PATH에 추가합니다.
    Claude CLI / Codex CLI와 동일한 방식으로 설치됩니다.

.EXAMPLE
    # GitHub Releases에서 최신 버전 설치:
    irm https://raw.githubusercontent.com/yourorg/agent-factory/main/install-af.ps1 | iex

    # 로컬 zip 파일로 설치:
    .\install-af.ps1 -ZipPath .\af-codex-5.4.zip

.PARAMETER ZipPath
    로컬 zip 파일 경로 (없으면 GitHub에서 다운로드)

.PARAMETER InstallDir
    설치 디렉토리 (기본: $env:LOCALAPPDATA\AgentFactory)

.PARAMETER Version
    설치할 버전 태그 (기본: 최신 릴리즈)
#>

param(
    [string]$ZipPath = "",
    [string]$InstallDir = "$env:LOCALAPPDATA\AgentFactory",
    [string]$Version = "latest",
    [string]$Repo = "hoonkim1092-web/agent-factory"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 색상 헬퍼 ─────────────────────────────────────────────────────────────────
function Write-Step  { param([string]$msg) Write-Host "  $msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$msg) Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "  ! $msg" -ForegroundColor Yellow }
function Write-Fail  { param([string]$msg) Write-Host "  ✗ $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "======================================" -ForegroundColor Blue
Write-Host "   Agent Factory (af) Installer" -ForegroundColor Blue
Write-Host "======================================" -ForegroundColor Blue
Write-Host ""

# ── 1. zip 파일 확보 ──────────────────────────────────────────────────────────
$tempZip = ""
if ($ZipPath -ne "") {
    if (-not (Test-Path $ZipPath)) {
        Write-Fail "zip 파일을 찾을 수 없습니다: $ZipPath"
        exit 1
    }
    $tempZip = (Resolve-Path $ZipPath).Path
    Write-Step "로컬 zip 사용: $tempZip"
} else {
    Write-Step "GitHub Releases에서 다운로드 중..."

    # 릴리즈 URL 결정
    if ($Version -eq "latest") {
        $apiUrl = "https://api.github.com/repos/$Repo/releases/latest"
    } else {
        $apiUrl = "https://api.github.com/repos/$Repo/releases/tags/$Version"
    }

    try {
        $release = Invoke-RestMethod -Uri $apiUrl -Headers @{ "User-Agent" = "af-installer" }
        $asset = $release.assets | Where-Object { $_.name -like "af-*.zip" } | Select-Object -First 1
        if (-not $asset) {
            Write-Fail "릴리즈 에셋에서 af-*.zip을 찾을 수 없습니다."
            exit 1
        }
        $downloadUrl = $asset.browser_download_url
        $tempZip = Join-Path $env:TEMP $asset.name

        Write-Step "다운로드: $($asset.name) ($([math]::Round($asset.size/1MB, 1)) MB)"
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tempZip -UseBasicParsing
        Write-OK "다운로드 완료"
    } catch {
        Write-Fail "다운로드 실패: $_"
        Write-Warn "로컬 zip 파일로 설치하려면: .\install-af.ps1 -ZipPath .\af-codex-5.4.zip"
        exit 1
    }
}

# ── 2. 설치 디렉토리 준비 ─────────────────────────────────────────────────────
Write-Step "설치 디렉토리 준비: $InstallDir"
if (Test-Path $InstallDir) {
    Write-Warn "기존 설치 덮어씁니다: $InstallDir"
    Remove-Item -Recurse -Force $InstallDir
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# ── 3. 압축 해제 ──────────────────────────────────────────────────────────────
Write-Step "압축 해제 중..."
$tempExtract = Join-Path $env:TEMP "af_install_$(Get-Random)"
New-Item -ItemType Directory -Force -Path $tempExtract | Out-Null

try {
    Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force
} catch {
    Write-Fail "압축 해제 실패: $_"
    exit 1
}

# zip 내부 구조 감지 (dist/af/ 또는 루트 직접)
$afSubDir = Get-ChildItem -Path $tempExtract -Recurse -Filter "af.exe" | Select-Object -First 1
if (-not $afSubDir) {
    Write-Fail "압축 파일 내에서 af.exe를 찾을 수 없습니다."
    exit 1
}
$sourceDir = $afSubDir.DirectoryName

# 내용물 복사
Copy-Item -Path (Join-Path $sourceDir "*") -Destination $InstallDir -Recurse -Force
Write-OK "압축 해제 완료"

# 임시 파일 정리
Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
if ($ZipPath -eq "" -and (Test-Path $tempZip)) {
    Remove-Item -Force $tempZip -ErrorAction SilentlyContinue
}

# ── 4. PATH 등록 ──────────────────────────────────────────────────────────────
Write-Step "PATH 등록 중..."

$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable(
        "PATH",
        "$InstallDir;$currentPath",
        "User"
    )
    # 현재 세션에도 반영
    $env:PATH = "$InstallDir;$env:PATH"
    Write-OK "PATH에 추가됨 (현재 세션 + 영구)"
} else {
    Write-OK "이미 PATH에 있습니다"
}

# ── 5. 버전 확인 ──────────────────────────────────────────────────────────────
$exePath = Join-Path $InstallDir "af.exe"
Write-Step "설치 확인..."
try {
    $verOutput = & $exePath --version 2>&1
    Write-OK "af 실행 확인: $verOutput"
} catch {
    Write-Warn "af --version 실행 실패 (PATH 재시작 후 정상 작동할 수 있음)"
}

# ── 6. API 키 안내 ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "   설치 완료!" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "  설치 경로: $InstallDir" -ForegroundColor White
Write-Host ""
Write-Host "  API 키 설정 (선택사항):" -ForegroundColor Yellow
Write-Host '    $env:GEMINI_API_KEY = "your-key"     # Google Gemini (스킬 생성)' -ForegroundColor Gray
Write-Host '    $env:OPENAI_API_KEY = "your-key"     # OpenAI GPT' -ForegroundColor Gray
Write-Host '    $env:ANTHROPIC_API_KEY = "your-key"  # Claude' -ForegroundColor Gray
Write-Host ""
Write-Host "  CLI 공급자 (키 없이 Claude CLI 사용):" -ForegroundColor Yellow
Write-Host '    $env:AGENT_CHAT_PROVIDER = "claude"  # claude CLI 사용' -ForegroundColor Gray
Write-Host ""
Write-Host "  시작하기:" -ForegroundColor Cyan
Write-Host "    af --help" -ForegroundColor White
Write-Host "    af -p my_project -t `"피보나치 수열 코드 작성해줘`"" -ForegroundColor White
Write-Host ""
Write-Host "  새 터미널을 열어야 PATH가 적용됩니다." -ForegroundColor Yellow
Write-Host ""
