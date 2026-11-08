param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$DataRoot = 'C:\AIAssets\INCLUDE_videos',
  [string]$Manifest = '.\outputs\include50_official_val_manifest.csv',
  [string]$CaptureDir = '.\outputs\browser_canvas_validation',
  [string]$OutputDir = '.\outputs\phase5_canvas_pixel_audit'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
  throw "Project Python 3.12 environment not found: $Python. Do not rebuild or replace it for this diagnostic."
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$pythonVersion = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12') { throw "Expected Python 3.12; found '$pythonVersion'." }
foreach ($entry in @(@{Path=$RepoPath;Name='AI4Bharat repository'}, @{Path=$DataRoot;Name='INCLUDE source videos'}, @{Path=$CaptureDir;Name='saved browser Canvas captures'})) {
  if (-not (Test-Path -LiteralPath $entry.Path -PathType Container)) { throw "$($entry.Name) directory missing: $($entry.Path)" }
}
if ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes|on)$') { throw 'Process environment ISL_IOT_ENABLED is true. Phase 5 is blocked.' }
$env:ISL_IOT_ENABLED = 'false'
if (Test-Path -LiteralPath '.\.env' -PathType Leaf) {
  if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$' -Quiet) {
    throw '.env contains ISL_IOT_ENABLED=true. Stop and correct it; this runner will not edit your .env.'
  }
}
$iot = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
if (@($iot.mappings.PSObject.Properties).Count -ne 0) { throw 'IoT mapping is not empty. Phase 5 requires zero mappings.' }
$settingsSource = Get-Content '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') { throw 'Hard pre-IoT lockdown not found in application config.' }

function Resolve-ProjectPath([string]$Value) {
  if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
  return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
}
$ManifestPath = Resolve-ProjectPath $Manifest
$CapturePath = Resolve-ProjectPath $CaptureDir
$OutputPath = Resolve-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  Write-Host 'Official manifest not found; generating from the pinned INCLUDE repository...' -ForegroundColor Cyan
  & $Python '.\scripts\prepare_official_validation_manifest.py' --repo $RepoPath --output $ManifestPath
  if ($LASTEXITCODE -ne 0) { throw 'Official validation manifest creation failed.' }
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogRoot = Join-Path $ProjectRoot "outputs\phase5_run_logs_$Stamp"
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$TestLog = Join-Path $LogRoot 'pytest_full_suite.log'
$RunLog = Join-Path $LogRoot 'phase5_runner.log'
$MetaLog = Join-Path $LogRoot 'run_environment.txt'
@(
  "started_local=$((Get-Date).ToString('o'))"
  "project_root=$ProjectRoot"
  "python=$Python"
  "python_version=$pythonVersion"
  "repo=$RepoPath"
  "data_root=$DataRoot"
  "manifest=$ManifestPath"
  "capture_dir=$CapturePath"
  "output_dir=$OutputPath"
  'iot_enabled=false'
  'iot_mapping_count=0'
  'classifier_checkpoint_loaded=false'
  'production_code_modified=false'
) | Set-Content -LiteralPath $MetaLog -Encoding UTF8

Write-Host 'Running the complete project test suite before Phase 5...' -ForegroundColor Cyan
& $Python -m pytest -q *> $TestLog
$testExit = $LASTEXITCODE
Get-Content -LiteralPath $TestLog -Tail 18 | ForEach-Object { Write-Host $_ }
if ($testExit -ne 0) { throw "Full test suite failed (exit $testExit). Phase 5 was not started. Full log: $TestLog" }

Write-Host 'Running Phase 5 pixel-level Canvas audit on six clips...' -ForegroundColor Cyan
# MediaPipe/TFLite can emit harmless native stderr messages. Continue while the
# process runs and evaluate Python's actual exit code after it completes.
$previousErrorActionPreference = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue'
  & $Python '.\scripts\phase5_canvas_pixel_audit.py' `
    --repo $RepoPath `
    --data-root $DataRoot `
    --manifest $ManifestPath `
    --capture-dir $CapturePath `
    --output-dir $OutputPath *> $RunLog
  $runExit = $LASTEXITCODE
}
finally {
  $ErrorActionPreference = $previousErrorActionPreference
}
Get-Content -LiteralPath $RunLog -Tail 30 | ForEach-Object { Write-Host $_ }
if ($runExit -ne 0) { throw "Phase 5 runner failed (exit $runExit). Full log: $RunLog" }

$required = @(
  'phase5_summary.json',
  'PHASE5_CANVAS_PIXEL_AUDIT_REPORT.md',
  'phase5_frame_pixel_metrics.csv',
  'phase5_pixel_transform_summary.csv',
  'phase5_best_pixel_match_per_frame.csv',
  'phase5_static_detector_frames.csv',
  'phase5_static_detector_pairwise.csv',
  'phase5_clip_metadata.csv'
)
foreach ($file in $required) {
  $full = Join-Path $OutputPath $file
  if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "Phase 5 output missing: $full" }
}

$Stage = Join-Path $ProjectRoot "outputs\PHASE5_BUNDLE_STAGE_$Stamp"
$Bundle = Join-Path $ProjectRoot "outputs\PHASE5_RESULT_BUNDLE_$Stamp.zip"
New-Item -ItemType Directory -Path (Join-Path $Stage 'results') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage 'logs') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage 'metadata') -Force | Out-Null
foreach ($file in $required) { Copy-Item -LiteralPath (Join-Path $OutputPath $file) -Destination (Join-Path $Stage 'results') }
Copy-Item -LiteralPath $TestLog -Destination (Join-Path $Stage 'logs')
Copy-Item -LiteralPath $RunLog -Destination (Join-Path $Stage 'logs')
Copy-Item -LiteralPath $MetaLog -Destination (Join-Path $Stage 'metadata')
$hashLines = foreach ($f in (Get-ChildItem -LiteralPath $Stage -File -Recurse | Sort-Object FullName)) {
  $h = Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256
  "$($h.Hash)  $($f.FullName.Substring($Stage.Length + 1))"
}
$hashLines | Set-Content -LiteralPath (Join-Path $Stage 'SHA256SUMS.txt') -Encoding UTF8
if (Test-Path -LiteralPath $Bundle) { Remove-Item -LiteralPath $Bundle -Force }
Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $Bundle -CompressionLevel Optimal
$zipCheck = & $Python -c "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); bad=z.testzip(); print('ZIP_TEST=' + ('OK' if bad is None else bad)); sys.exit(0 if bad is None else 3)" $Bundle
$zipExit = $LASTEXITCODE
Write-Host $zipCheck
if ($zipExit -ne 0) { throw 'Phase 5 result bundle failed ZIP CRC verification.' }
$bundleHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash
Write-Host ''
Write-Host 'PHASE 5 COMPLETED' -ForegroundColor Green
Write-Host "RESULT_BUNDLE=$Bundle"
Write-Host "BUNDLE_SHA256=$bundleHash"
Write-Host "REPORT=$(Join-Path $OutputPath 'PHASE5_CANVAS_PIXEL_AUDIT_REPORT.md')"
Write-Host "TEST_LOG=$TestLog"
Write-Host 'IoT stayed disabled. The classifier/checkpoint was not loaded. No production setting was changed.' -ForegroundColor Green
