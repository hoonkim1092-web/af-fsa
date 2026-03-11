@echo off
setlocal
"C:\Users\HOME\AppData\Local\Python\pythoncore-3.14-64\python.exe" "D:\warkSpaces\agent-factory\scripts\destructive_guard_proxy.py" --target powershell --delegate "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.EXE" -- %*
exit /b %ERRORLEVEL%
