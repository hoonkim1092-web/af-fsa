@echo off
@chcp 65001 >nul
echo [Logi-Mind Agent Factory] Adding to User PATH...

powershell -Command "$Target='d:\agent-factory'; $Current=[System.Environment]::GetEnvironmentVariable('Path', 'User'); if ($Current -notlike \"*$Target*\") { [System.Environment]::SetEnvironmentVariable('Path', $Current + \";$Target\", 'User'); Write-Host '✅ PATH updated!' } else { Write-Host 'ℹ️ Already in PATH.' }"

echo.
echo ✅ 설정이 완료되었습니다!
echo 이제 열려 있는 터미널을 모두 닫고 다시 열면,
echo 어디서든 'agent' 명령어를 사용할 수 있습니다.
echo.
pause
