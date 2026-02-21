@echo off
setlocal

set ACTION=%1
if "%ACTION%"=="" set ACTION=up
set BACKEND=%2
if "%BACKEND%"=="" set BACKEND=git
set TARGET=%3
if "%TARGET%"=="" set TARGET=all
set AGENT=%4

if "%AGENT%"=="" (
  powershell -ExecutionPolicy Bypass -File "%~dp0scripts\sync_easy.ps1" -Action %ACTION% -Backend %BACKEND% -Target %TARGET%
) else (
  powershell -ExecutionPolicy Bypass -File "%~dp0scripts\sync_easy.ps1" -Action %ACTION% -Backend %BACKEND% -Target %TARGET% -Agent %AGENT%
)
exit /b %ERRORLEVEL%

