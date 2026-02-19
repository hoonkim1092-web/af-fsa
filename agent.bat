@echo off
@chcp 65001 >nul

set "FACTORY_DIR=d:\agent-factory"
set "CLI_SCRIPT=%FACTORY_DIR%\run_factory_cli.py"

rem Prefer a real Python binary over WindowsApps shim.
set "PYTHON_EXE="
if exist "%LOCALAPPDATA%\Python\bin\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%CLI_SCRIPT%" %*
if errorlevel 1 (
    echo.
    echo [ERROR] Agent execution failed.
    pause
)
