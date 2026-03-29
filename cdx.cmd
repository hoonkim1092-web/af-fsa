@echo off
setlocal EnableExtensions
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set LANG=C.UTF-8
set LC_ALL=C.UTF-8

set "CODEX_PATH="
for /f "delims=" %%I in ('where codex 2^>nul') do if not defined CODEX_PATH set "CODEX_PATH=%%~fI"

if not defined CODEX_PATH goto :not_found

if /i "%CDX_DEBUG%"=="1" (
  echo [CDX] codex path: "%CODEX_PATH%"
  echo [CDX] args: %*
)

if defined CDX_LOG_FILE call :log start %*
call "%CODEX_PATH%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if defined CDX_LOG_FILE call :log exit %EXIT_CODE%

if exist "%~dp0scripts\codex_session_bridge.py" (
  if /i "%CDX_DEBUG%"=="1" (
    python "%~dp0scripts\codex_session_bridge.py" --repo-root "%~dp0"
  ) else (
    python "%~dp0scripts\codex_session_bridge.py" --repo-root "%~dp0" >nul 2>nul
  )
  set "BRIDGE_EXIT=%ERRORLEVEL%"
  if /i "%CDX_DEBUG%"=="1" echo [CDX] bridge exit: %BRIDGE_EXIT%
)

exit /b %EXIT_CODE%

:not_found
echo [CDX] 'codex' command not found in PATH.
echo [CDX] Install Codex CLI first, then run: cdx
echo [CDX] Optional: set CDX_DEBUG=1 or CDX_LOG_FILE=path
exit /b 1

:log
setlocal
>>"%CDX_LOG_FILE%" echo [%DATE% %TIME%] %*
endlocal
exit /b 0
