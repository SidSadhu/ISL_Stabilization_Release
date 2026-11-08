param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$DataRoot = 'C:\AIAssets\INCLUDE_videos',
  [string]$Manifest = '.\outputs\include50_official_val_manifest.csv',
  [string]$Phase5Dir = '.\outputs\phase5_canvas_pixel_audit'
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

foreach ($entry in @(
  @{Path=$RepoPath;Name='AI4Bharat repository'},
  @{Path=$DataRoot;Name='INCLUDE source videos'}
)) {
  if (-not (Test-Path -LiteralPath $entry.Path -PathType Container)) { throw "$($entry.Name) directory missing: $($entry.Path)" }
}
if ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes|on)$') { throw 'Process environment ISL_IOT_ENABLED is true. Phase 6 is blocked.' }
$env:ISL_IOT_ENABLED = 'false'
if (Test-Path -LiteralPath '.\.env' -PathType Leaf) {
  if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$' -Quiet) {
    throw '.env contains ISL_IOT_ENABLED=true. Stop and correct it; this runner will not edit your .env.'
  }
}
$iot = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
if (@($iot.mappings.PSObject.Properties).Count -ne 0) { throw 'IoT mapping is not empty. Phase 6 requires zero mappings.' }
$settingsSource = Get-Content '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') { throw 'Hard pre-IoT lockdown not found in application config.' }

function Resolve-ProjectPath([string]$Value) {
  if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
  return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
}
$ManifestPath = Resolve-ProjectPath $Manifest
$Phase5Path = Resolve-ProjectPath $Phase5Dir
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  throw "Official 77-item validation manifest missing: $ManifestPath. Generate it with the existing prepare_official_validation_manifest.py first."
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$CapturePath = Join-Path $ProjectRoot "outputs\browser_canvas_validation_phase6_rvfc_$Stamp"
$OutputPath = Join-Path $ProjectRoot "outputs\phase6_rvfc_alignment_$Stamp"
$LogRoot = Join-Path $ProjectRoot "outputs\phase6_run_logs_$Stamp"
$Stage = Join-Path $ProjectRoot "outputs\PHASE6_BUNDLE_STAGE_$Stamp"
$Bundle = Join-Path $ProjectRoot "outputs\PHASE6_RESULT_BUNDLE_$Stamp.zip"
New-Item -ItemType Directory -Path $LogRoot,$CapturePath,$OutputPath -Force | Out-Null
$TestLog = Join-Path $LogRoot 'pytest_full_suite.log'
$ServerStdout = Join-Path $LogRoot 'phase6_capture_server.stdout.log'
$ServerStderr = Join-Path $LogRoot 'phase6_capture_server.stderr.log'
$AnalysisLog = Join-Path $LogRoot 'phase6_analysis.log'
$RunnerMeta = Join-Path $LogRoot 'run_environment.txt'

@(
  "started_local=$((Get-Date).ToString('o'))"
  "project_root=$ProjectRoot"
  "python=$Python"
  "python_version=$pythonVersion"
  "repo=$RepoPath"
  "data_root=$DataRoot"
  "manifest=$ManifestPath"
  "phase5_dir=$Phase5Path"
  "capture_dir=$CapturePath"
  "output_dir=$OutputPath"
  'capture_sampling_mode=requestVideoFrameCallback'
  'capture_timestamp=metadata.mediaTime'
  'draw_synchronous_inside_callback=true'
  'iot_enabled=false'
  'iot_mapping_count=0'
  'classifier_checkpoint_loaded=false'
  'production_code_modified=false'
) | Set-Content -LiteralPath $RunnerMeta -Encoding UTF8

Write-Host 'Running the complete project test suite before Phase 6...' -ForegroundColor Cyan
& $Python -m pytest -q *> $TestLog
$testExit = $LASTEXITCODE
Get-Content -LiteralPath $TestLog -Tail 16 | ForEach-Object { Write-Host $_ }
if ($testExit -ne 0) { throw "Full test suite failed (exit $testExit). Phase 6 capture was not started. Full log: $TestLog" }

Write-Host ''
Write-Host 'Starting the local frame-synchronous browser capture server...' -ForegroundColor Cyan
$ServerArgs = @(
  '-u',
  '.\scripts\serve_phase6_rvfc_capture.py',
  '--manifest', $ManifestPath,
  '--repo', $RepoPath,
  '--output', $CapturePath,
  '--port', '8766',
  '--open-browser'
)
$Server = Start-Process -FilePath $Python -ArgumentList $ServerArgs -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $ServerStdout -RedirectStandardError $ServerStderr

try {
  $serverReady = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($Server.HasExited) {
      $tail = if (Test-Path $ServerStderr) { (Get-Content $ServerStderr -Raw) } else { '' }
      throw "Phase 6 capture server exited before becoming ready. $tail"
    }
    try {
      $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/status' -TimeoutSec 3
      if ($status.expected_count -eq 77) { $serverReady = $true; break }
    } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $serverReady) { throw "Phase 6 capture server did not become ready. See $ServerStdout and $ServerStderr" }

  Write-Host ''
  Write-Host 'BROWSER ACTION REQUIRED:' -ForegroundColor Yellow
  Write-Host '1. In the opened page, select the folder C:\AIAssets\INCLUDE_videos.' -ForegroundColor Yellow
  Write-Host '2. Confirm that all six diagnostic clips are matched.' -ForegroundColor Yellow
  Write-Host '3. Click "Capture six clips with frame callbacks".' -ForegroundColor Yellow
  Write-Host 'Keep this PowerShell window open. It will continue automatically after 6/6 captures.' -ForegroundColor Yellow

  $TargetPaths = @(
    'Greetings/51. Good Morning/MVI_0099.MOV',
    'Colours/55. White/MVI_5063.MOV',
    'Adjectives/83. big large/MVI_9396.MOV',
    'Adjectives/79. short/MVI_9302.MOV',
    'Means_of_Transportation/11. Car/MVI_3118.MOV',
    'Pronouns/40. I/MVI_9922.MOV'
  )
  $TargetSet = @{}
  foreach ($p in $TargetPaths) { $TargetSet[$p.ToLowerInvariant()] = $true }
  $lastCount = -1
  $deadline = (Get-Date).AddMinutes(20)
  while ((Get-Date) -lt $deadline) {
    if ($Server.HasExited) { throw "Phase 6 capture server exited during capture. Inspect $ServerStdout and $ServerStderr" }
    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/status' -TimeoutSec 5
    $receivedTargets = @($status.received | Where-Object {
      $key = ([string]$_.video_path).Replace('\','/').ToLowerInvariant()
      $TargetSet.ContainsKey($key)
    })
    $count = $receivedTargets.Count
    if ($count -ne $lastCount) {
      Write-Host "Phase 6 browser captures received: $count / 6" -ForegroundColor Cyan
      $lastCount = $count
    }
    if ($count -eq 6) { break }
    Start-Sleep -Seconds 2
  }
  if ($lastCount -ne 6) { throw "Timed out waiting for all six browser captures. Received $lastCount/6. Captures are retained at $CapturePath" }
}
finally {
  if ($Server -and -not $Server.HasExited) {
    Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
    $Server.WaitForExit()
  }
}

$clipFiles = @(Get-ChildItem -LiteralPath (Join-Path $CapturePath 'clips') -Filter '*.islbrw' -File)
if ($clipFiles.Count -ne 6) { throw "Expected exactly six RVFC capture containers; found $($clipFiles.Count). Directory: $CapturePath" }

Write-Host ''
Write-Host 'Running PTS alignment, pixel comparison and static MediaPipe replay...' -ForegroundColor Cyan
# MediaPipe/TensorFlow Lite can emit benign native stderr messages. In Windows
# PowerShell, NativeCommandError records can become terminating when the global
# preference is Stop. Continue only around this native process and use its real
# exit code as the success/failure authority; preserve all output in AnalysisLog.
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
Get-Content -LiteralPath $AnalysisLog -Tail 28 | ForEach-Object { Write-Host $_ }
if ($analysisExit -ne 0) { throw "Phase 6 analysis failed (exit $analysisExit). Full log: $AnalysisLog. Captures are retained at $CapturePath" }

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
  if (-not (Test-Path -LiteralPath (Join-Path $OutputPath $file) -PathType Leaf)) { throw "Phase 6 output missing: $file" }
}

New-Item -ItemType Directory -Path (Join-Path $Stage 'results'),(Join-Path $Stage 'logs'),(Join-Path $Stage 'metadata'),(Join-Path $Stage 'captures\clips') -Force | Out-Null
foreach ($file in $required) { Copy-Item -LiteralPath (Join-Path $OutputPath $file) -Destination (Join-Path $Stage 'results') }
Copy-Item -LiteralPath (Join-Path $CapturePath 'capture_index.json') -Destination (Join-Path $Stage 'captures')
Get-ChildItem -LiteralPath (Join-Path $CapturePath 'clips') -Filter '*.islbrw' -File | Copy-Item -Destination (Join-Path $Stage 'captures\clips')
Copy-Item -LiteralPath $TestLog,$ServerStdout,$ServerStderr,$AnalysisLog -Destination (Join-Path $Stage 'logs')
Copy-Item -LiteralPath $RunnerMeta -Destination (Join-Path $Stage 'metadata')
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
if ($zipExit -ne 0) { throw 'Phase 6 result bundle failed ZIP CRC verification.' }
$bundleHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash

Write-Host ''
Write-Host 'PHASE 6 COMPLETED' -ForegroundColor Green
Write-Host "RESULT_BUNDLE=$Bundle"
Write-Host "BUNDLE_SHA256=$bundleHash"
Write-Host "REPORT=$(Join-Path $OutputPath 'PHASE6_RVFC_ALIGNMENT_REPORT.md')"
Write-Host "CAPTURE_DIR=$CapturePath"
Write-Host "TEST_LOG=$TestLog"
Write-Host 'IoT stayed disabled. The classifier/checkpoint was not loaded. No production setting was changed.' -ForegroundColor Green
