param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$DataRoot = 'C:\AIAssets\INCLUDE_videos',
  [string]$Manifest = '.\outputs\include50_official_val_manifest.csv',
  [string]$Phase5Dir = '.\outputs\phase5_canvas_pixel_audit',
  [string]$CaptureDir = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
  throw "Project Python 3.12 environment not found: $Python. Do not rebuild or replace it."
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$pythonVersion = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12') { throw "Expected Python 3.12; found '$pythonVersion'." }

function Resolve-ProjectPath([string]$Value) {
  if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
  return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
}
$ManifestPath = Resolve-ProjectPath $Manifest
$Phase5Path = Resolve-ProjectPath $Phase5Dir
if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) { throw "AI4Bharat repo missing: $RepoPath" }
if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) { throw "INCLUDE data root missing: $DataRoot" }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Official validation manifest missing: $ManifestPath" }

if ([string]::IsNullOrWhiteSpace($CaptureDir)) {
  $candidates = @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'outputs') -Directory -Filter 'browser_canvas_validation_phase6_rvfc_*' -ErrorAction Stop |
    Sort-Object LastWriteTime -Descending)
  $selected = $null
  foreach ($candidate in $candidates) {
    $index = Join-Path $candidate.FullName 'capture_index.json'
    $clips = @(Get-ChildItem -LiteralPath (Join-Path $candidate.FullName 'clips') -Filter '*.islbrw' -File -ErrorAction SilentlyContinue)
    if ((Test-Path -LiteralPath $index -PathType Leaf) -and $clips.Count -eq 6) { $selected = $candidate; break }
  }
  if (-not $selected) { throw 'No saved Phase 6 capture directory with capture_index.json and exactly six .islbrw files was found.' }
  $CapturePath = $selected.FullName
} else {
  $CapturePath = Resolve-ProjectPath $CaptureDir
}
if (-not (Test-Path -LiteralPath (Join-Path $CapturePath 'capture_index.json') -PathType Leaf)) {
  throw "capture_index.json missing from saved capture directory: $CapturePath"
}
$clipFiles = @(Get-ChildItem -LiteralPath (Join-Path $CapturePath 'clips') -Filter '*.islbrw' -File)
if ($clipFiles.Count -ne 6) { throw "Expected six saved RVFC captures; found $($clipFiles.Count): $CapturePath" }

# Keep all safety checks strict. This recovery reuses captures and does not recapture or change the app.
if ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes|on)$') { throw 'Process environment ISL_IOT_ENABLED is true. Recovery is blocked.' }
$env:ISL_IOT_ENABLED = 'false'
if (Test-Path -LiteralPath '.\.env' -PathType Leaf) {
  if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$' -Quiet) {
    throw '.env contains ISL_IOT_ENABLED=true. Stop; this script will not edit .env.'
  }
}
$iot = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
if (@($iot.mappings.PSObject.Properties).Count -ne 0) { throw 'IoT mapping must remain empty.' }
$settingsSource = Get-Content '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') { throw 'Hard pre-IoT lockdown not found.' }

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$OutputPath = Join-Path $ProjectRoot "outputs\phase6_rvfc_alignment_recovered_$Stamp"
$LogRoot = Join-Path $ProjectRoot "outputs\phase6_recovery_logs_$Stamp"
$Stage = Join-Path $ProjectRoot "outputs\PHASE6_BUNDLE_STAGE_$Stamp"
$Bundle = Join-Path $ProjectRoot "outputs\PHASE6_RESULT_BUNDLE_$Stamp.zip"
New-Item -ItemType Directory -Path $OutputPath,$LogRoot,(Join-Path $Stage 'results'),(Join-Path $Stage 'logs'),(Join-Path $Stage 'metadata'),(Join-Path $Stage 'captures\clips') -Force | Out-Null
$AnalysisLog = Join-Path $LogRoot 'phase6_analysis_retry.log'
$MetaLog = Join-Path $LogRoot 'recovery_environment.txt'

$priorRun = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'outputs') -Directory -Filter 'phase6_run_logs_*' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
$priorTestLog = if ($priorRun) { Join-Path $priorRun.FullName 'pytest_full_suite.log' } else { $null }
if (-not $priorTestLog -or -not (Test-Path -LiteralPath $priorTestLog -PathType Leaf)) {
  throw 'Could not find the original Phase 6 pytest_full_suite.log. The capture can still be retained, but this recovery bundle requires its test evidence.'
}

@(
  "recovered_local=$((Get-Date).ToString('o'))"
  "project_root=$ProjectRoot"
  "python=$Python"
  "python_version=$pythonVersion"
  "repo=$RepoPath"
  "data_root=$DataRoot"
  "manifest=$ManifestPath"
  "phase5_dir=$Phase5Path"
  "capture_dir=$CapturePath"
  "output_dir=$OutputPath"
  'recovery_mode=analysis_only_from_existing_rvfc_captures'
  'browser_recapture=false'
  'full_test_suite_rerun=false; see original pytest_full_suite.log'
  'iot_enabled=false'
  'iot_mapping_count=0'
  'classifier_checkpoint_loaded=false'
  'production_code_modified=false'
) | Set-Content -LiteralPath $MetaLog -Encoding UTF8

Write-Host 'Reusing the six saved Phase 6 browser captures; no browser recapture is needed.' -ForegroundColor Cyan
Write-Host "CAPTURE_DIR=$CapturePath"
Write-Host 'Running PTS alignment, pixel comparison and static MediaPipe replay...' -ForegroundColor Cyan
$previousErrorActionPreference = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue'
  & $Python '.\scripts\phase6_rvfc_alignment_diagnostics.py' `
    --repo $RepoPath `
    --data-root $DataRoot `
    --manifest $ManifestPath `
    --capture-dir $CapturePath `
    --phase5-dir $Phase5Path `
    --output-dir $OutputPath *> $AnalysisLog
  $analysisExit = $LASTEXITCODE
}
finally {
  $ErrorActionPreference = $previousErrorActionPreference
}
Get-Content -LiteralPath $AnalysisLog -Tail 32 | ForEach-Object { Write-Host $_ }
if ($analysisExit -ne 0) { throw "Phase 6 analysis failed (exit $analysisExit). Full log: $AnalysisLog. Original captures remain at $CapturePath" }

$required = @(
  'phase6_summary.json',
  'PHASE6_RVFC_ALIGNMENT_REPORT.md',
  'phase6_frame_timing.csv',
  'phase6_frame_pixel_metrics.csv',
  'phase6_best_pixel_match_per_frame.csv',
  'phase6_clip_summary.csv',
  'phase6_static_detector_frames.csv',
  'phase6_static_detector_pairwise.csv'
)
foreach ($file in $required) {
  if (-not (Test-Path -LiteralPath (Join-Path $OutputPath $file) -PathType Leaf)) { throw "Phase 6 output missing: $file. Full log: $AnalysisLog" }
  Copy-Item -LiteralPath (Join-Path $OutputPath $file) -Destination (Join-Path $Stage 'results')
}
Copy-Item -LiteralPath (Join-Path $CapturePath 'capture_index.json') -Destination (Join-Path $Stage 'captures')
Get-ChildItem -LiteralPath (Join-Path $CapturePath 'clips') -Filter '*.islbrw' -File | Copy-Item -Destination (Join-Path $Stage 'captures\clips')
Copy-Item -LiteralPath $priorTestLog -Destination (Join-Path $Stage 'logs')
Copy-Item -LiteralPath $AnalysisLog -Destination (Join-Path $Stage 'logs')
Copy-Item -LiteralPath $MetaLog -Destination (Join-Path $Stage 'metadata')
if ($priorRun) {
  foreach ($name in @('phase6_capture_server.stdout.log','phase6_capture_server.stderr.log')) {
    $candidate = Join-Path $priorRun.FullName $name
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { Copy-Item -LiteralPath $candidate -Destination (Join-Path $Stage 'logs') }
  }
}
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
if ($zipExit -ne 0) { throw 'Phase 6 recovery result bundle failed ZIP CRC verification.' }
$bundleHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash
Write-Host ''
Write-Host 'PHASE 6 COMPLETED FROM SAVED CAPTURES' -ForegroundColor Green
Write-Host "RESULT_BUNDLE=$Bundle"
Write-Host "BUNDLE_SHA256=$bundleHash"
Write-Host "REPORT=$(Join-Path $OutputPath 'PHASE6_RVFC_ALIGNMENT_REPORT.md')"
Write-Host "CAPTURE_DIR=$CapturePath"
Write-Host 'No browser recapture. No model/checkpoint load. IoT disabled. No production setting changed.' -ForegroundColor Green
