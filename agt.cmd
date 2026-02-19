@echo off
setlocal

chcp 65001 >nul
set "ROOT=%~dp0"

if exist "%ROOT%antigravity_link.py" (
  python "%ROOT%antigravity_link.py" %*
  exit /b %ERRORLEVEL%
)

echo [AGT] antigravity_link.py not found: %ROOT%
exit /b 1
