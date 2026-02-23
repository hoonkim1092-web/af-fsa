@echo off
setlocal EnableExtensions

set "TARGET=agent-factory"
set "CUR=%CD%"
set "TARGET_PATH="

:search_up
if exist "%CUR%\%TARGET%\.git" (
    set "TARGET_PATH=%CUR%\%TARGET%"
    goto run_or_print
)

for %%I in ("%CUR%") do set "PARENT=%%~dpI"
set "PARENT=%PARENT:~0,-1%"

if /I "%PARENT%"=="%CUR%" goto search_env
set "CUR=%PARENT%"
goto search_up

:search_env
if defined HOON_PROJECTS_HOME (
    if exist "%HOON_PROJECTS_HOME%\%TARGET%\.git" (
        set "TARGET_PATH=%HOON_PROJECTS_HOME%\%TARGET%"
        goto run_or_print
    )
)

if defined PROJECTS_HOME (
    if exist "%PROJECTS_HOME%\%TARGET%\.git" (
        set "TARGET_PATH=%PROJECTS_HOME%\%TARGET%"
        goto run_or_print
    )
)

echo [af] Could not find "%TARGET%". Move under a parent that contains it or set HOON_PROJECTS_HOME.
exit /b 1

:run_or_print
if "%~1"=="" (
    echo %TARGET_PATH%
    exit /b 0
)

pushd "%TARGET_PATH%" >nul 2>nul
if errorlevel 1 (
    echo [af] Failed to enter "%TARGET_PATH%".
    exit /b 1
)

call %*
set "ERR=%ERRORLEVEL%"
popd >nul
exit /b %ERR%
