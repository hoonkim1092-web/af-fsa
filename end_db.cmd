@echo off
setlocal
call "%~dp0end_sync.cmd" db %*
exit /b %ERRORLEVEL%
