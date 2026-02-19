param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Targets
)

if (-not $Targets -or $Targets.Count -eq 0) {
  Write-Error "Usage: .\\scripts\\safe_py_compile.ps1 <file1.py> [file2.py ...]"
  exit 2
}

$env:PYTHONPYCACHEPREFIX = Join-Path $env:LOCALAPPDATA "Temp\\pycache_codex"
$env:PYTHONUTF8 = "1"
$pythonExe = "C:\Users\HOME\AppData\Local\Python\bin\python.exe"

if (-not (Test-Path $pythonExe)) {
  Write-Error "Python executable not found: $pythonExe"
  exit 3
}

& $pythonExe -m py_compile @Targets
exit $LASTEXITCODE
