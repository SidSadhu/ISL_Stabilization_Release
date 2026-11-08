$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
if (Test-Path '.\.venv\Scripts\python.exe') { $Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path } else { $Python = (Get-Command python).Source }
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
