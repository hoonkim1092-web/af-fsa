@echo off
setlocal

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo [UTF8] code page set to 65001
echo [UTF8] PYTHONUTF8=1, PYTHONIOENCODING=utf-8
echo [UTF8] Run this in the same terminal before viewing Korean text.
