@echo off
setlocal

set TARGET=%1
if "%TARGET%"=="" set TARGET=all
set AGENT=%2

echo [SYNC START] pulling latest context from Git + DB for target=%TARGET% agent=%AGENT%...
call sync down git %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%

call sync down db %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%

echo [SYNC START] done.
exit /b 0
