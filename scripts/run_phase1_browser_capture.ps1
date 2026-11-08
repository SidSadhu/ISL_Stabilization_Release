param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$Manifest = '.\outputs\include50_official_val_manifest.csv',
  [string]$OutputDir = '.\outputs\browser_canvas_validation',
  [int]$Port = 8765,
  [switch]$Overwrite
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
  throw 'Project .venv not found. Create/use the existing target Windows Python 3.12 environment first.'
}
$Python = (Resolve-Path -LiteralPath $PythonPath).Path
$pythonMinor = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonMinor -ne '3.12') {
  throw "Target environment must be Python 3.12; found '$pythonMinor'. No environment was rebuilt."
}
if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) { throw "AI4Bharat repo not found: $RepoPath" }
if (-not (Test-Path -LiteralPath '.\.env' -PathType Leaf)) {
  Write-Host '.env is absent; app default for ISL_IOT_ENABLED is false. The receiver does not import or start the app.' -ForegroundColor Yellow
} elseif ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes)$') { throw 'Process environment has ISL_IOT_ENABLED=true. Set it to false before diagnostics.' }
if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes)\s*$' -Quiet) {
  throw '.env has ISL_IOT_ENABLED=true. Set it to false before diagnostics.'
}
$settingsSource = Get-Content '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') { throw 'Hard-coded pre-IoT lockdown was not found.' }
$iotConfig = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
$mappingProperties = @($iotConfig.mappings.PSObject.Properties)
if ($mappingProperties.Count -ne 0) { throw 'IoT label map must remain empty during diagnostics.' }

function Get-ProjectPath([string]$Value) {
  if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
  return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
}
$ManifestPath = Get-ProjectPath $Manifest
$OutputPath = Get-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  Write-Host 'Generating the exact pinned official 77-item validation manifest...' -ForegroundColor Cyan
  & $Python '.\scripts\prepare_official_validation_manifest.py' --repo $RepoPath --output $ManifestPath
  if ($LASTEXITCODE -ne 0) { throw 'Official validation manifest preparation failed.' }
}
if (-not (1024 -le $Port -and $Port -le 65535)) { throw 'Port must be between 1024 and 65535.' }
$argsList = @(
  '.\scripts\serve_browser_validation_capture.py',
  '--manifest', $ManifestPath,
  '--repo', (Get-ProjectPath $RepoPath),
  '--output', $OutputPath,
  '--port', [string]$Port,
  '--max-upload-mb', '512',
  '--open-browser'
)
if ($Overwrite) { $argsList += '--overwrite' }
Write-Host 'Starting local-only browser Canvas capture. IoT/MQTT/app endpoints are not started.' -ForegroundColor Cyan
Write-Host "Capture page: http://127.0.0.1:$Port/"
Write-Host 'Choose the directory containing the original official validation videos; the page requires all 77 paths to match.'
& $Python @argsList
$serverExit = $LASTEXITCODE
if ($serverExit -ne 0) {
  throw "Browser capture receiver exited with code $serverExit. Review capture_index.json and resume; do not claim a complete benchmark."
}
Write-Host "Capture receiver completed. Review $OutputPath\capture_index.json before running the evaluator." -ForegroundColor Green
