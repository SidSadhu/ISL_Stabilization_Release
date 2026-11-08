param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$CheckpointPath = 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth',
  [string]$VideoRoot = 'C:\AIAssets\INCLUDE_videos',
  [string]$Manifest = '.\outputs\include50_official_val_manifest.csv',
  [string]$CaptureDir = '.\outputs\browser_canvas_validation',
  [string]$Phase1OutputDir = '.\outputs\browser_canvas_evaluation',
  [switch]$RunPhase1
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
  throw 'Project .venv not found. Run the Windows setup instructions with 64-bit Python 3.12; this runner never creates/rebuilds the environment.'
}
$Python = (Resolve-Path -LiteralPath $PythonPath).Path
$pythonMinor = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonMinor -ne '3.12') { throw "Target verification requires Python 3.12; found '$pythonMinor'." }

if (-not (Test-Path -LiteralPath '.\.env.example' -PathType Leaf)) { throw '.env.example is missing from the package.' }
if (-not (Test-Path -LiteralPath '.\.env' -PathType Leaf)) {
  Copy-Item '.\.env.example' '.\.env'
  Write-Host 'Created .env from the safe project template. Review local asset paths before running the app.' -ForegroundColor Yellow
}
if ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes)$') { throw 'Process environment has ISL_IOT_ENABLED=true. Set it to false before diagnostics.' }
if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes)\s*$' -Quiet) {
  throw '.env has ISL_IOT_ENABLED=true. Set it to false; diagnostics will not proceed.'
}
$settingsSource = Get-Content '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') { throw 'Hard-coded application pre-IoT lock is missing.' }
$iotConfig = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
$mappingProperties = @($iotConfig.mappings.PSObject.Properties)
if ($mappingProperties.Count -ne 0) { throw 'config/iot_label_map.json must contain zero mappings throughout diagnostics.' }

$started = Get-Date
Write-Host '1/7 — Verify release SHA256 manifest' -ForegroundColor Cyan
& $Python '.\scripts\verify_release_checksums.py'
if ($LASTEXITCODE -ne 0) { throw 'Release checksum verification failed.' }

Write-Host '2/7 — Compile all Python modules' -ForegroundColor Cyan
& $Python -m compileall -q .\src .\scripts .\tests
if ($LASTEXITCODE -ne 0) { throw 'Python compileall failed.' }

Write-Host '3/7 — Check active JavaScript syntax' -ForegroundColor Cyan
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  foreach ($js in @('.\src\isl_recognition\static\app.js', '.\src\isl_recognition\static\landmark_diagnostics.js', '.\src\isl_recognition\static\landmark_worker.js')) {
    & $node.Source --check $js
    if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed: $js" }
  }
} else {
  Write-Host 'Node.js is not installed; JavaScript parser check was not run.' -ForegroundColor Yellow
}

Write-Host '4/7 — Run the full regression suite' -ForegroundColor Cyan
& $Python -m pytest -q -rs
if ($LASTEXITCODE -ne 0) { throw 'Python regression suite failed.' }

Write-Host '5/7 — Run dependency and external asset preflight' -ForegroundColor Cyan
& $Python '.\scripts\preflight.py'
if ($LASTEXITCODE -ne 0) { throw 'Preflight command failed.' }

$phase1Status = 'NOT_RUN — use run_phase1_browser_capture.ps1, then pass -RunPhase1 after 77/77 captures exist.'
if ($RunPhase1) {
  Write-Host '6/7 — Verify manifest, assets and execute Phase 1 browser Canvas evaluator' -ForegroundColor Cyan
  function Get-ProjectPath([string]$Value) {
    if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
  }
  $manifestFull = Get-ProjectPath $Manifest
  if (-not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) {
    & $Python '.\scripts\prepare_official_validation_manifest.py' --repo $RepoPath --output $manifestFull
    if ($LASTEXITCODE -ne 0) { throw 'Official 77-item manifest creation failed.' }
  }
  $captureFull = Get-ProjectPath $CaptureDir
  $clipCount = if (Test-Path -LiteralPath (Join-Path $captureFull 'clips')) { @(Get-ChildItem (Join-Path $captureFull 'clips') -Filter '*.islbrw' -File).Count } else { 0 }
  if ($clipCount -ne 77) { throw "Phase 1 requires exactly 77 browser capture containers; found $clipCount. Run run_phase1_browser_capture.ps1 first." }
  $outputFull = Get-ProjectPath $Phase1OutputDir
  & $Python '.\scripts\evaluate_browser_canvas_validation.py' `
    --manifest $manifestFull `
    --capture-dir $captureFull `
    --data-root $VideoRoot `
    --repo $RepoPath `
    --checkpoint $CheckpointPath `
    --text-threshold 0.40 `
    --out-dir $outputFull
  if ($LASTEXITCODE -ne 0) { throw 'Phase 1 evaluator failed or was blocked. Do not report benchmark metrics.' }
  $phase1Status = 'COMPLETED — inspect metrics.json, per_clip.csv and both confusion matrices.'
} else {
  Write-Host '6/7 — Phase 1 evaluator not requested. No recognition metrics are produced by this release-check run.' -ForegroundColor Yellow
}

Write-Host '7/7 — Final IoT-off safety assertion' -ForegroundColor Cyan
if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes)\s*$' -Quiet) { throw 'IoT became enabled during the run; fail closed.' }

$report = [ordered]@{
  generated_at = (Get-Date).ToString('o')
  project_version = (Get-Content '.\RELEASE_VERSION.txt' -Raw).Trim()
  python_version = (& $Python --version 2>&1).ToString()
  phase0_checks = 'PASS'
  python_tests = 'Executed; see console output for exact count and skip reason.'
  static_checks = 'Python compileall PASS; JavaScript PASS when Node.js is present.'
  pre_iot_lockdown = $true
  iot_enabled = $false
  default_iot_mapping_count = 0
  phase1 = $phase1Status
  elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)
}
$reportPath = Join-Path $ProjectRoot 'outputs\stabilization_release_check.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $reportPath) | Out-Null
$report | ConvertTo-Json -Depth 6 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host "Release check completed. Run record: $reportPath" -ForegroundColor Green
if (-not $RunPhase1) {
  Write-Host 'Recognition release remains NOT GO until Phase 1/2/3/4/5 evidence and target-Windows checks are complete.' -ForegroundColor Yellow
}
