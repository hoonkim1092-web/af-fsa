@echo off
setlocal

chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON_EXE=C:\Users\HOME\AppData\Local\Python\bin\python.exe"

if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=py"
)

if /I "%PYTHON_EXE%"=="py" (
  py -3 --version >nul 2>nul
  if errorlevel 1 (
    set "PYTHON_EXE=python"
  )
)

if /I "%PYTHON_EXE%"=="python" (
  python --version >nul 2>nul
  if errorlevel 1 (
    echo [AGT] Python executable not found. Install Python or set PYTHON_EXE manually.
    exit /b 1
  )
)

if exist "%ROOT%antigravity_link.py" (
  "%PYTHON_EXE%" "%ROOT%antigravity_link.py" %*
  exit /b %ERRORLEVEL%
)

echo [AGT] antigravity_link.py not found: %ROOT%
exit /b 1
