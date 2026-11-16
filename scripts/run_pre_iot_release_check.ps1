$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonPath)) { throw 'Project virtual environment not found. Run .\scripts\setup_windows.ps1 first.' }
$Python = (Resolve-Path $PythonPath).Path
Write-Host '1/5 - Python compilation' -ForegroundColor Cyan
& $Python -m compileall -q .\src .\scripts .\tests
if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
Write-Host '2/5 - JavaScript syntax' -ForegroundColor Cyan
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  & node.Source --check .\src\isl_recognition\static\app.js
  if ($LASTEXITCODE -ne 0) { throw 'app.js syntax check failed.' }
  & node.Source --check .\src\isl_recognition\static\landmark_diagnostics.js
  if ($LASTEXITCODE -ne 0) { throw 'landmark_diagnostics.js syntax check failed.' }
  Write-Host 'Browser JavaScript syntax passed.' -ForegroundColor Green
} else { Write-Host 'Node.js not installed; skipping JavaScript parser check.' -ForegroundColor Yellow }
Write-Host '3/5 - Unit and API tests' -ForegroundColor Cyan
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Test suite failed.' }
Write-Host '4/5 - Dependency and asset preflight' -ForegroundColor Cyan
& $Python .\scripts\preflight.py
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed.' }
Write-Host '5/5 - Pre-IoT policy check' -ForegroundColor Cyan
$source = Get-Content .\src\isl_recognition\app_config.py -Raw
if ($source -notmatch 'pre_iot_lockdown: bool = True') { throw 'Pre-IoT code-level lockdown is not true.' }
if (Test-Path '.\.env') {
  $iotEnabled = Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*true\s*$' -Quiet
  if ($iotEnabled) { throw '.env must set ISL_IOT_ENABLED=false for the pre-IoT release.' }
}
Write-Host 'Pre-IoT release checks passed. This does not verify a physical webcam, external model assets, or production recognition accuracy.' -ForegroundColor Green
