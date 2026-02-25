@echo off
setlocal
call "%~dp0start_sync.cmd" db %*
exit /b %ERRORLEVEL%
