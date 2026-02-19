@echo off
setlocal

set TARGET=%1
if "%TARGET%"=="" set TARGET=all
set AGENT=%2

echo [SYNC END] pushing latest context to DB + Git for target=%TARGET% agent=%AGENT%...
call sync up db %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%

call sync up git %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%

echo [SYNC END] done.
exit /b 0
