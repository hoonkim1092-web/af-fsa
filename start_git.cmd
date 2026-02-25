@echo off
setlocal
call "%~dp0start_sync.cmd" git %*
exit /b %ERRORLEVEL%
