@echo off
setlocal
call "%~dp0end_sync.cmd" git %*
exit /b %ERRORLEVEL%
