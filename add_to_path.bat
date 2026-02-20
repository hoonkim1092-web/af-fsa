@echo off
setlocal
chcp 65001 >nul

set "TARGET=%~dp0"
if "%TARGET:~-1%"=="\" set "TARGET=%TARGET:~0,-1%"

echo [CDX] Adding "%TARGET%" to user PATH...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$target = '%TARGET%'; $current = [Environment]::GetEnvironmentVariable('Path','User'); if ([string]::IsNullOrWhiteSpace($current)) { [Environment]::SetEnvironmentVariable('Path', $target, 'User'); Write-Host '[CDX] PATH updated.' } elseif ($current -split ';' | Where-Object { $_.Trim() -ieq $target }) { Write-Host '[CDX] Already in PATH.' } else { [Environment]::SetEnvironmentVariable('Path', $current + ';' + $target, 'User'); Write-Host '[CDX] PATH updated.' }"

echo.
echo [CDX] Done. Open a new terminal, then run: cdx --help
echo.
pause
