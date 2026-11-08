[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepoPath,
    [Parameter(Mandatory=$true)][string]$DataRoot,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [string]$FfprobePath = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Get-Location).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Preflight = Join-Path $ProjectRoot 'scripts\phase8_python_preflight.py'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Project virtual environment Python missing: $Python" }
if (-not (Test-Path -LiteralPath $Preflight -PathType Leaf)) { throw "Phase 8 Python preflight missing: $Preflight" }
$pythonVersion = (& $Python $Preflight).Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12') { throw "Expected Python 3.12; found '$pythonVersion'." }

foreach ($entry in @(
    @{Path=$RepoPath;Name='AI4Bharat repository'},
    @{Path=$DataRoot;Name='INCLUDE source videos'}
)) {
    if (-not (Test-Path -LiteralPath $entry.Path -PathType Container)) { throw "$($entry.Name) directory missing: $($entry.Path)" }
}
if (-not (Test-Path -LiteralPath '.\scripts\serve_browser_validation_capture.py' -PathType Leaf) -or
    -not (Test-Path -LiteralPath '.\scripts\browser_capture_protocol.py' -PathType Leaf)) {
    throw 'Validated browser capture receiver/protocol is missing.'
}
if ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes|on)$') { throw 'Process environment ISL_IOT_ENABLED is true. Phase 8 is blocked.' }
$env:ISL_IOT_ENABLED = 'false'
if (Test-Path -LiteralPath '.\.env' -PathType Leaf) {
    if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$' -Quiet) {
        throw '.env contains ISL_IOT_ENABLED=true. Phase 8 will not edit it.'
    }
}
$iot = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
if (@($iot.mappings.PSObject.Properties).Count -ne 0) { throw 'IoT mapping is not empty. Phase 8 requires zero mappings.' }
$settingsSource = Get-Content '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') { throw 'Hard pre-IoT lockdown not found.' }

function Resolve-ProjectPath([string]$Value) {
    if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
}
$ManifestPath = Resolve-ProjectPath $Manifest
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Official validation manifest missing: $ManifestPath" }
if ([string]::IsNullOrWhiteSpace($FfprobePath)) {
    $cmd = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($cmd) { $FfprobePath = $cmd.Source }
}
if ([string]::IsNullOrWhiteSpace($FfprobePath) -or -not (Test-Path -LiteralPath $FfprobePath -PathType Leaf)) {
    $packageRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $packageRoot) {
        $candidate = Get-ChildItem -LiteralPath $packageRoot -Directory -Filter 'Gyan.FFmpeg*' -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter 'ffprobe.exe' -File -Recurse -ErrorAction SilentlyContinue } |
            Select-Object -First 1
        if ($candidate) { $FfprobePath = $candidate.FullName }
    }
}
if ([string]::IsNullOrWhiteSpace($FfprobePath) -or -not (Test-Path -LiteralPath $FfprobePath -PathType Leaf)) {
    throw 'FFprobe was not found. Phase 8 requires the FFprobe used in Phase 6/7.'
}
# Use Start-Process with file redirection instead of a native-command pipeline.
# Windows PowerShell 5.1 can convert native stderr records into NativeCommandError
# when ErrorActionPreference is Stop, even when ffprobe itself is healthy.
$ProbeRoot = Join-Path $env:TEMP ("phase8_ffprobe_probe_" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ProbeRoot -Force | Out-Null
$ProbeStdout = Join-Path $ProbeRoot 'ffprobe.stdout.log'
$ProbeStderr = Join-Path $ProbeRoot 'ffprobe.stderr.log'
try {
    $probeProcess = Start-Process -FilePath $FfprobePath -ArgumentList @('-version') `
        -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $ProbeStdout -RedirectStandardError $ProbeStderr
    $probeExit = $probeProcess.ExitCode
    $probeStdoutText = if (Test-Path -LiteralPath $ProbeStdout) { Get-Content -LiteralPath $ProbeStdout -Raw } else { '' }
    $probeStderrText = if (Test-Path -LiteralPath $ProbeStderr) { Get-Content -LiteralPath $ProbeStderr -Raw } else { '' }
    $ffprobeVersionText = ($probeStdoutText + "`n" + $probeStderrText).Trim()
    if ($probeExit -ne 0) {
        throw "FFprobe version check failed with exit code $probeExit. Output: $ffprobeVersionText"
    }
    $versionMatch = [regex]::Match($ffprobeVersionText, '(?m)^ffprobe version [^\r\n]+')
    if (-not $versionMatch.Success) {
        throw "FFprobe returned exit code 0 but did not report a recognizable version. Output: $ffprobeVersionText"
    }
    $ffprobeVersion = $versionMatch.Value
}
finally {
    if (Test-Path -LiteralPath $ProbeRoot) {
        Remove-Item -LiteralPath $ProbeRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Modes = @('gate_sync','ungate_sync','gate_async','ungate_async')
$Ports = @{gate_sync=8781;ungate_sync=8782;gate_async=8783;ungate_async=8784}
$ModePaths = @{}
foreach ($mode in $Modes) {
    $ModePaths[$mode] = Join-Path $ProjectRoot "outputs\phase8_capture_${mode}_$Stamp"
    New-Item -ItemType Directory -Path $ModePaths[$mode] -Force | Out-Null
}
$OutputPath = Join-Path $ProjectRoot "outputs\phase8_callback_lifecycle_$Stamp"
$LogRoot = Join-Path $ProjectRoot "outputs\phase8_run_logs_$Stamp"
$Stage = Join-Path $ProjectRoot "outputs\PHASE8_BUNDLE_STAGE_$Stamp"
$Bundle = Join-Path $ProjectRoot "outputs\PHASE8_RESULT_BUNDLE_$Stamp.zip"
New-Item -ItemType Directory -Path $OutputPath,$LogRoot | Out-Null
$TestLog = Join-Path $LogRoot 'pytest_full_suite.log'
$RunnerMeta = Join-Path $LogRoot 'run_environment.txt'

@(
    "started_local=$((Get-Date).ToString('o'))"
    "project_root=$ProjectRoot"
    "python=$Python"
    "python_version=$pythonVersion"
    "repo=$RepoPath"
    "data_root=$DataRoot"
    "manifest=$ManifestPath"
    "ffprobe=$FfprobePath"
    "ffprobe_version=$ffprobeVersion"
    "analysis_output_dir=$OutputPath"
    'factorial_modes=gate_sync,ungate_sync,gate_async,ungate_async'
    'canvas_width=640'
    'jpeg_quality=0.92'
    'drawImage_synchronous_inside_callback=true'
    'visibility_focus_playback_heartbeat_instrumentation=true'
    'source_pts=ffprobe_best_effort_timestamp_time'
    'iot_enabled=false'
    'classifier_checkpoint_loaded=false'
    'production_code_modified=false'
) | Set-Content -LiteralPath $RunnerMeta -Encoding UTF8

Write-Host 'Running the complete project test suite before Phase 8...' -ForegroundColor Cyan
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $Python -m pytest -q *> $TestLog
$testExit = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference
Get-Content -LiteralPath $TestLog -Tail 25 | ForEach-Object { Write-Host $_ }
if ($testExit -ne 0) { throw "Complete project test suite failed (exit $testExit). Full log: $TestLog" }

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

function Invoke-Phase8CapturePass([string]$Mode, [int]$Port) {
    $Destination = $ModePaths[$Mode]
    $stdout = Join-Path $LogRoot "${Mode}_server.stdout.log"
    $stderr = Join-Path $LogRoot "${Mode}_server.stderr.log"
    $serverArgs = @(
        '-u', '.\scripts\serve_phase8_capture.py',
        '--manifest', $ManifestPath,
        '--repo', $RepoPath,
        '--output', $Destination,
        '--mode', $Mode,
        '--port', [string]$Port,
        '--max-upload-mb', '512',
        '--open-browser'
    )
    $server = Start-Process -FilePath $Python -ArgumentList $serverArgs -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    try {
        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            if ($server.HasExited) {
                $tail = if (Test-Path $stderr) { Get-Content $stderr -Raw } else { '' }
                throw "Phase 8 $Mode capture server exited before ready. $tail"
            }
            try {
                $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 3
                if ($status.expected_count -eq 77) { $ready = $true; break }
            } catch { Start-Sleep -Seconds 1 }
        }
        if (-not $ready) { throw "Phase 8 $Mode capture server did not become ready. See $stdout and $stderr" }
        Write-Host ''
        Write-Host "BROWSER ACTION REQUIRED - $Mode" -ForegroundColor Yellow
        Write-Host '1. Select C:\AIAssets\INCLUDE_videos in the opened browser tab.' -ForegroundColor Yellow
        Write-Host '2. Confirm all six diagnostic clips are matched.' -ForegroundColor Yellow
        Write-Host "3. Click the capture button for $Mode." -ForegroundColor Yellow
        Write-Host 'Keep this PowerShell window open until this pass reaches 6/6.' -ForegroundColor Yellow
        $lastCount = -1
        $deadline = (Get-Date).AddMinutes(20)
        while ((Get-Date) -lt $deadline) {
            if ($server.HasExited) { throw "Phase 8 $Mode server exited during capture. Inspect $stdout and $stderr" }
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 5
            $received = @($status.received | Where-Object {
                $key = ([string]$_.video_path).Replace('\','/').ToLowerInvariant()
                $TargetSet.ContainsKey($key)
            })
            $count = $received.Count
            if ($count -ne $lastCount) {
                Write-Host "Phase 8 $Mode captures received: $count / 6" -ForegroundColor Cyan
                $lastCount = $count
            }
            if ($count -eq 6) { break }
            Start-Sleep -Seconds 2
        }
        if ($lastCount -ne 6) { throw "Timed out waiting for Phase 8 $Mode captures. Received $lastCount/6. Data remains at $Destination" }
    }
    finally {
        if ($server -and -not $server.HasExited) {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
            $server.WaitForExit()
        }
    }
    $files = @(Get-ChildItem -LiteralPath (Join-Path $Destination 'clips') -Filter '*.islbrw' -File)
    if ($files.Count -ne 6) { throw "Expected six Phase 8 $Mode capture containers; found $($files.Count). Directory: $Destination" }
}

Invoke-Phase8CapturePass -Mode 'gate_sync' -Port $Ports['gate_sync']
Invoke-Phase8CapturePass -Mode 'ungate_sync' -Port $Ports['ungate_sync']
Invoke-Phase8CapturePass -Mode 'gate_async' -Port $Ports['gate_async']
Invoke-Phase8CapturePass -Mode 'ungate_async' -Port $Ports['ungate_async']

Write-Host ''
Write-Host 'Running FFprobe alignment and Phase 8 factorial analysis...' -ForegroundColor Cyan
$AnalysisLog = Join-Path $LogRoot 'phase8_analysis.log'
$analysisArgs = @(
    '.\scripts\phase8_callback_lifecycle_analysis.py',
    '--repo', $RepoPath,
    '--data-root', $DataRoot,
    '--manifest', $ManifestPath,
    '--ffprobe', $FfprobePath,
    '--output-dir', $OutputPath,
    '--gate-sync-dir', $ModePaths['gate_sync'],
    '--ungate-sync-dir', $ModePaths['ungate_sync'],
    '--gate-async-dir', $ModePaths['gate_async'],
    '--ungate-async-dir', $ModePaths['ungate_async']
)
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $Python @analysisArgs *> $AnalysisLog
$analysisExit = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference
Get-Content -LiteralPath $AnalysisLog -Tail 40 | ForEach-Object { Write-Host $_ }
if ($analysisExit -ne 0) { throw "Phase 8 analysis failed (exit $analysisExit). Full log: $AnalysisLog" }

$Required = @(
    'phase8_summary.json','PHASE8_CALLBACK_LIFECYCLE_REPORT.md','phase8_clip_metrics.csv',
    'phase8_frame_metrics.csv','phase8_event_timeline.csv','phase8_playback_heartbeats.csv',
    'phase8_factorial_effects.csv','phase8_per_clip_factor_effects.csv','phase8_source_pts_metadata.csv'
)
foreach ($file in $Required) {
    if (-not (Test-Path -LiteralPath (Join-Path $OutputPath $file) -PathType Leaf)) { throw "Phase 8 output missing: $file" }
}

New-Item -ItemType Directory -Path (Join-Path $Stage 'results'),(Join-Path $Stage 'logs'),(Join-Path $Stage 'metadata') -Force | Out-Null
foreach ($file in $Required) { Copy-Item -LiteralPath (Join-Path $OutputPath $file) -Destination (Join-Path $Stage 'results') }
foreach ($mode in $Modes) {
    New-Item -ItemType Directory -Path (Join-Path $Stage "captures\$mode\clips") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $ModePaths[$mode] 'capture_index.json') -Destination (Join-Path $Stage "captures\$mode")
    Get-ChildItem -LiteralPath (Join-Path $ModePaths[$mode] 'clips') -Filter '*.islbrw' -File | Copy-Item -Destination (Join-Path $Stage "captures\$mode\clips")
}
Get-ChildItem -LiteralPath $LogRoot -File | Copy-Item -Destination (Join-Path $Stage 'logs')
Copy-Item -LiteralPath $RunnerMeta -Destination (Join-Path $Stage 'metadata')
$hashLines = foreach ($f in (Get-ChildItem -LiteralPath $Stage -File -Recurse | Sort-Object FullName)) {
    $h = Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256
    "$($h.Hash)  $($f.FullName.Substring($Stage.Length + 1).Replace('\','/'))"
}
$hashLines | Set-Content -LiteralPath (Join-Path $Stage 'SHA256SUMS.txt') -Encoding UTF8
if (Test-Path -LiteralPath $Bundle) { Remove-Item -LiteralPath $Bundle -Force }
Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $Bundle -CompressionLevel Optimal
$VerifyPath = Join-Path $ProjectRoot 'scripts\verify_phase8_bundle.py'
$verifyOutput = & $Python $VerifyPath $Bundle
$verifyExit = $LASTEXITCODE
$verifyOutput | ForEach-Object { Write-Host $_ }
if ($verifyExit -ne 0) { throw 'Phase 8 result bundle failed integrity verification.' }
$bundleHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash

Write-Host ''
Write-Host 'PHASE 8 COMPLETED' -ForegroundColor Green
Write-Host "RESULT_BUNDLE=$Bundle"
Write-Host "BUNDLE_SHA256=$bundleHash"
Write-Host "REPORT=$(Join-Path $OutputPath 'PHASE8_CALLBACK_LIFECYCLE_REPORT.md')"
Write-Host "TEST_LOG=$TestLog"
Write-Host 'IoT stayed disabled. Classifier/checkpoint was not loaded. No production code or settings were changed.' -ForegroundColor Green
