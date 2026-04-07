# Create shortcuts directory
$shortcutsDir = "d:\shortcuts"
if (!(Test-Path -Path $shortcutsDir)) {
    New-Item -ItemType Directory -Path $shortcutsDir -Force | Out-Null
}

# Create .bat files for CMD
Set-Content -Path "$shortcutsDir\af.bat" -Value "@echo off`r`nd:`r`ncd d:\agent-factory" -Encoding ASCII
Set-Content -Path "$shortcutsDir\lm.bat" -Value "@echo off`r`nd:`r`ncd d:\logi-mind-v22" -Encoding ASCII

Write-Host "Created .bat shortcuts in $shortcutsDir"

# Add to User PATH if not exists
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notmatch [regex]::Escape($shortcutsDir)) {
    $newPath = $userPath + ";$shortcutsDir"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Host "Added $shortcutsDir to User PATH."
} else {
    Write-Host "$shortcutsDir already in User PATH."
}

# Add aliases to PowerShell Profile
if (!(Test-Path -Path $PROFILE)) {
    New-Item -ItemType File -Path $PROFILE -Force | Out-Null
}
$profileContent = Get-Content -Path $PROFILE -Encoding utf8 -ErrorAction SilentlyContinue
$afExists = $profileContent -match "function af "
$lmExists = $profileContent -match "function lm "

if (!$afExists -or !$lmExists) {
    Add-Content -Path $PROFILE -Value "`n# Directory Shortcuts" -Encoding utf8
    Add-Content -Path $PROFILE -Value "function af { Set-Location `'d:\agent-factory`' }" -Encoding utf8
    Add-Content -Path $PROFILE -Value "function lm { Set-Location `'d:\logi-mind-v22`' }" -Encoding utf8
    Write-Host "Added 'af' and 'lm' functions to PowerShell Profile ($PROFILE)."
} else {
    Write-Host "PowerShell profile already contains shortcuts."
}

# Add aliases to Git Bash (.bashrc)
$bashrcPath = "$env:USERPROFILE\.bashrc"
if (!(Test-Path -Path $bashrcPath)) {
    New-Item -ItemType File -Path $bashrcPath -Force | Out-Null
}
$bashrcContent = Get-Content -Path $bashrcPath -Encoding utf8 -ErrorAction SilentlyContinue
$bashAfExists = $bashrcContent -match "alias af="
$bashLmExists = $bashrcContent -match "alias lm="

if (!$bashAfExists -or !$bashLmExists) {
    Add-Content -Path $bashrcPath -Value "`n# Directory Shortcuts" -Encoding utf8
    Add-Content -Path $bashrcPath -Value "alias af='cd /d/agent-factory'" -Encoding utf8
    Add-Content -Path $bashrcPath -Value "alias lm='cd /d/logi-mind-v22'" -Encoding utf8
    Write-Host "Added 'af' and 'lm' aliases to Git Bash ~/.bashrc."
} else {
    Write-Host "Git Bash ~/.bashrc already contains shortcuts."
}

Write-Host "Done!"
