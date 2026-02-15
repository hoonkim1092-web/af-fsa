@echo off
@chcp 65001 >nul
:: agent.bat - Logi-Mind Agent Factory Wrapper

:: Factory 경로 설정을 위해 이 파일이 있는 곳을 기준으로 하거나 하드코딩 필요
:: 여기서는 d:\agent-factory가 고정되어 있다고 가정합니다.
set "FACTORY_DIR=d:\agent-factory"
set "CLI_SCRIPT=%FACTORY_DIR%\run_factory_cli.py"

:: Python 실행 (시스템 PATH에 python이 있다고 가정)
python "%CLI_SCRIPT%" %*

if errorlevel 1 (
    echo.
    echo ⚠️ 에이전트 실행 중 오류가 발생했습니다.
    pause
)
