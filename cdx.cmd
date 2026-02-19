@echo off
setlocal

chcp 65001 >nul

where codex >nul 2>nul
if %ERRORLEVEL%==0 (
  codex %*
  exit /b %ERRORLEVEL%
)

echo [CDX] 'codex' command not found in PATH.
echo [CDX] Install Codex CLI first, then run: cdx
exit /b 1
