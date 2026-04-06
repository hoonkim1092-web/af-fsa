@echo off
setlocal
"C:\Users\HOME\AppData\Local\Python\pythoncore-3.14-64\python.exe" "D:\warkSpaces\agent-factory\scripts\destructive_guard_proxy.py" --target pwsh --delegate "pwsh" -- %*
exit /b %ERRORLEVEL%
