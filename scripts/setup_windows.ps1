param(
  [switch]$CoreOnly,
  [switch]$SkipVenv
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "ISL project setup - $ProjectRoot" -ForegroundColor Cyan
if (-not $SkipVenv -and -not (Test-Path '.\.venv\Scripts\python.exe')) {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    & py -3.12 --version
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 was not found. Install 64-bit Python 3.12 from python.org and retry.' }
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating .venv failed.' }
  } else {
    & python --version
    if ((& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") -ne '3.12') { throw 'Install Python 3.12 (64-bit) and make it available on PATH.' }
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating .venv failed.' }
  }
}
if (-not $SkipVenv) {
  if (-not (Test-Path '.\.venv\Scripts\python.exe')) { throw 'Virtual environment was not created. Install Python 3.12 x64 and retry.' }
  $Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
} else { $Python = (Get-Command python).Source }
& $Python --version
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed; inspect the pip error above.' }
& $Python -m pip install -r requirements-web.txt
if ($LASTEXITCODE -ne 0) { throw 'Installing requirements-web.txt failed.' }
if (-not $CoreOnly) {
  foreach ($requirements in @('requirements-mediapipe.txt','requirements-mqtt.txt','requirements-model.txt','requirements-evaluation.txt','requirements-dev.txt')) {
    & $Python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Installing $requirements failed; resolve the reported package/platform conflict before continuing." }
  }
}
if (-not (Test-Path '.\.env')) { Copy-Item '.\.env.example' '.\.env' }
& $Python .\scripts\preflight.py
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed.' }
Write-Host ''
Write-Host 'Setup command completed. Inspect the preflight output; model assets are not bundled.' -ForegroundColor Green
if ($CoreOnly) { Write-Host 'Core-only mode: install MediaPipe/MQTT/model requirements before testing those integrations.' -ForegroundColor Yellow }
