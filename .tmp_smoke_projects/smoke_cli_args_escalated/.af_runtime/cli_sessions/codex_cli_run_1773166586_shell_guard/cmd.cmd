@echo off
setlocal
"C:\Users\HOME\AppData\Local\Python\pythoncore-3.14-64\python.exe" "D:\warkSpaces\agent-factory\scripts\destructive_guard_proxy.py" --target cmd --delegate "C:\WINDOWS\system32\cmd.exe" -- %*
exit /b %ERRORLEVEL%
