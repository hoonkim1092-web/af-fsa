@echo off
setlocal

set BACKEND=%1
if /I "%BACKEND%"=="git" goto parsed_backend
if /I "%BACKEND%"=="db" goto parsed_backend
if /I "%BACKEND%"=="all" goto parsed_backend

set BACKEND=all
set TARGET=%1
if "%TARGET%"=="" set TARGET=all
set AGENT=%2
goto run

:parsed_backend
set TARGET=%2
if "%TARGET%"=="" set TARGET=all
set AGENT=%3

:run
if /I "%BACKEND%"=="all" goto run_all

echo [SYNC START] pulling latest context from %BACKEND% for target=%TARGET% agent=%AGENT%...
call sync down %BACKEND% %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%
echo [SYNC START] done.
exit /b 0

:run_all
echo [SYNC START] pulling latest context from Git + DB for target=%TARGET% agent=%AGENT%...
call sync down git %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%

call sync down db %TARGET% %AGENT%
if errorlevel 1 exit /b %errorlevel%

echo [SYNC START] done.
exit /b 0
