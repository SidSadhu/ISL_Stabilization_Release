[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,
    [Parameter(Mandatory = $true)]
    [string]$DataRoot,
    [Parameter(Mandatory = $true)]
    [string]$Manifest,
    [string]$FfprobePath = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Get-Location).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project virtual environment Python missing: $Python"
}

foreach ($item in @(
    @{ Path = $RepoPath; Name = 'AI4Bharat repository' },
    @{ Path = $DataRoot; Name = 'INCLUDE source videos' }
)) {
    if (-not (Test-Path -LiteralPath $item.Path -PathType Container)) {
        throw ("{0} directory missing: {1}" -f $item.Name, $item.Path)
    }
}

if (-not (Test-Path -LiteralPath '.\scripts\serve_browser_validation_capture.py' -PathType Leaf)) {
    throw 'The validated browser capture receiver is missing.'
}
if (-not (Test-Path -LiteralPath '.\scripts\browser_capture_protocol.py' -PathType Leaf)) {
    throw 'The validated browser capture protocol is missing.'
}
if ($env:ISL_IOT_ENABLED -match '^(?i:true|1|yes|on)$') {
    throw 'Process environment ISL_IOT_ENABLED is true. Phase 7 is blocked.'
}
$env:ISL_IOT_ENABLED = 'false'

if (Test-Path -LiteralPath '.\.env' -PathType Leaf) {
    $iotEnabledInEnv = Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$' -Quiet
    if ($iotEnabledInEnv) {
        throw '.env contains ISL_IOT_ENABLED=true. Correct it manually; the launcher will not edit .env.'
    }
}

$iotConfig = Get-Content -LiteralPath '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
$mappingCount = @($iotConfig.mappings.PSObject.Properties).Count
if ($mappingCount -ne 0) {
    throw 'IoT mapping is not empty. Phase 7 requires zero mappings.'
}
$settingsSource = Get-Content -LiteralPath '.\src\isl_recognition\app_config.py' -Raw
if ($settingsSource -notmatch 'pre_iot_lockdown:\s*bool\s*=\s*True') {
    throw 'Hard pre-IoT lockdown not found in application config.'
}

if ([System.IO.Path]::IsPathRooted($Manifest)) {
    $ManifestPath = [System.IO.Path]::GetFullPath($Manifest)
}
else {
    $ManifestPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Manifest))
}
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Official validation manifest missing: $ManifestPath"
}

if ([string]::IsNullOrWhiteSpace($FfprobePath)) {
    $ffprobeCommand = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($null -ne $ffprobeCommand) {
        $FfprobePath = $ffprobeCommand.Source
    }
}
if ([string]::IsNullOrWhiteSpace($FfprobePath) -or -not (Test-Path -LiteralPath $FfprobePath -PathType Leaf)) {
    $packageRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $packageRoot) {
        $packageDirs = Get-ChildItem -LiteralPath $packageRoot -Directory -Filter 'Gyan.FFmpeg*' -ErrorAction SilentlyContinue
        foreach ($packageDir in $packageDirs) {
            $ffprobeCandidate = Get-ChildItem -LiteralPath $packageDir.FullName -Filter 'ffprobe.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -ne $ffprobeCandidate) {
                $FfprobePath = $ffprobeCandidate.FullName
                break
            }
        }
    }
}
if ([string]::IsNullOrWhiteSpace($FfprobePath) -or -not (Test-Path -LiteralPath $FfprobePath -PathType Leaf)) {
    throw 'FFprobe was not found. Phase 7 requires the FFprobe executable used in Phase 6.'
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$OutputsRoot = Join-Path $ProjectRoot 'outputs'
$ModeAPath = Join-Path $OutputsRoot ("phase7_capture_current_40ms_{0}" -f $Stamp)
$ModeBPath = Join-Path $OutputsRoot ("phase7_capture_every_presented_frame_{0}" -f $Stamp)
$OutputPath = Join-Path $OutputsRoot ("phase7_capture_completeness_{0}" -f $Stamp)
$LogRoot = Join-Path $OutputsRoot ("phase7_run_logs_{0}" -f $Stamp)
$Stage = Join-Path $OutputsRoot ("PHASE7_BUNDLE_STAGE_{0}" -f $Stamp)
$Bundle = Join-Path $OutputsRoot ("PHASE7_RESULT_BUNDLE_{0}.zip" -f $Stamp)

foreach ($newDirectory in @($ModeAPath, $ModeBPath, $OutputPath, $LogRoot, $Stage)) {
    New-Item -ItemType Directory -Path $newDirectory -Force | Out-Null
}
foreach ($relativeDirectory in @(
    'results',
    'logs',
    'metadata',
    'captures\current_40ms\clips',
    'captures\every_presented_frame\clips'
)) {
    New-Item -ItemType Directory -Path (Join-Path $Stage $relativeDirectory) -Force | Out-Null
}

$TestLog = Join-Path $LogRoot 'pytest_full_suite.log'
$RunnerMeta = Join-Path $LogRoot 'run_environment.txt'
$VersionLog = Join-Path $LogRoot 'python_version.log'
$FfprobeLog = Join-Path $LogRoot 'ffprobe_version.log'

$VersionScript = Join-Path $ProjectRoot 'scripts\phase7_python_version.py'
if (-not (Test-Path -LiteralPath $VersionScript -PathType Leaf)) {
    throw "Standalone Python version probe missing: $VersionScript"
}
& $Python $VersionScript *> $VersionLog
$versionExit = $LASTEXITCODE
if ($versionExit -ne 0) {
    throw "Could not determine project Python version. See $VersionLog"
}
$pythonVersion = (Get-Content -LiteralPath $VersionLog -Raw).Trim()
if ($pythonVersion -ne '3.12') {
    throw "Expected Python 3.12; found '$pythonVersion'."
}

& $FfprobePath -version *> $FfprobeLog
$ffprobeExit = $LASTEXITCODE
if ($ffprobeExit -ne 0) {
    throw "FFprobe version check failed. See $FfprobeLog"
}
$ffprobeVersion = (Get-Content -LiteralPath $FfprobeLog -TotalCount 1)

$metadataLines = @(
    ('started_local={0}' -f (Get-Date).ToString('o')),
    ('project_root={0}' -f $ProjectRoot),
    ('python={0}' -f $Python),
    ('python_version={0}' -f $pythonVersion),
    ('repo={0}' -f $RepoPath),
    ('data_root={0}' -f $DataRoot),
    ('manifest={0}' -f $ManifestPath),
    ('ffprobe={0}' -f $FfprobePath),
    ('ffprobe_version={0}' -f $ffprobeVersion),
    ('mode_a_capture_dir={0}' -f $ModeAPath),
    ('mode_b_capture_dir={0}' -f $ModeBPath),
    ('analysis_output_dir={0}' -f $OutputPath),
    'mode_a=current_40ms: requestVideoFrameCallback; min_interval_ms=39; synchronous_toDataURL',
    'mode_b=every_presented_frame: requestVideoFrameCallback; no_min_interval; asynchronous_toBlob',
    'canvas_width=640',
    'jpeg_quality=0.92',
    'drawImage_synchronous_inside_callback=true',
    'ffprobe_pts_source=best_effort_timestamp_time',
    'iot_enabled=false',
    'iot_mapping_count=0',
    'classifier_checkpoint_loaded=false',
    'production_code_modified=false'
)
$metadataLines | Set-Content -LiteralPath $RunnerMeta -Encoding UTF8

Write-Host 'Running the complete project test suite before Phase 7...' -ForegroundColor Cyan
& $Python -m pytest -q *> $TestLog
$testExit = $LASTEXITCODE
Get-Content -LiteralPath $TestLog -Tail 18 | ForEach-Object { Write-Host $_ }
if ($testExit -ne 0) {
    throw "Full test suite failed (exit $testExit). Phase 7 capture was not started. Full log: $TestLog"
}

$TargetPaths = @(
    'Greetings/51. Good Morning/MVI_0099.MOV',
    'Colours/55. White/MVI_5063.MOV',
    'Adjectives/83. big large/MVI_9396.MOV',
    'Adjectives/79. short/MVI_9302.MOV',
    'Means_of_Transportation/11. Car/MVI_3118.MOV',
    'Pronouns/40. I/MVI_9922.MOV'
)
$TargetSet = @{}
foreach ($targetPath in $TargetPaths) {
    $TargetSet[$targetPath.ToLowerInvariant()] = $true
}

function ConvertTo-ProcessArgument {
    param([string]$Value)
    $escapedValue = $Value.Replace('"', '\"')
    return ('"{0}"' -f $escapedValue)
}

function Invoke-Phase7CapturePass {
    param(
        [string]$Mode,
        [string]$Destination,
        [int]$Port,
        [string]$PassName
    )

    $stdoutLog = Join-Path $LogRoot ("{0}_server.stdout.log" -f $Mode)
    $stderrLog = Join-Path $LogRoot ("{0}_server.stderr.log" -f $Mode)
    $serverScript = Join-Path $ProjectRoot 'scripts\serve_phase7_capture.py'
    $serverArguments = @(
        '-u',
        (ConvertTo-ProcessArgument $serverScript),
        '--manifest', (ConvertTo-ProcessArgument $ManifestPath),
        '--repo', (ConvertTo-ProcessArgument $RepoPath),
        '--output', (ConvertTo-ProcessArgument $Destination),
        '--mode', (ConvertTo-ProcessArgument $Mode),
        '--port', [string]$Port,
        '--max-upload-mb', '512',
        '--open-browser'
    )
    $argumentLine = [string]::Join(' ', $serverArguments)
    $serverProcess = $null
    $lastCount = -1

    try {
        $serverProcess = Start-Process -FilePath $Python -ArgumentList $argumentLine -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
        $ready = $false
        $statusUrl = 'http://127.0.0.1:{0}/api/status' -f $Port

        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            $serverProcess.Refresh()
            if ($serverProcess.HasExited) {
                $serverErrorText = ''
                if (Test-Path -LiteralPath $stderrLog) {
                    $serverErrorText = Get-Content -LiteralPath $stderrLog -Raw
                }
                throw ("Phase 7 {0} capture server exited before ready. {1}" -f $Mode, $serverErrorText)
            }
            try {
                $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 3
                if ([int]$status.expected_count -eq 77) {
                    $ready = $true
                    break
                }
            }
            catch {
                Start-Sleep -Seconds 1
            }
        }
        if (-not $ready) {
            throw ("Phase 7 {0} capture server did not become ready. See {1} and {2}" -f $Mode, $stdoutLog, $stderrLog)
        }

        Write-Host ''
        Write-Host ('BROWSER ACTION REQUIRED - {0}' -f $PassName) -ForegroundColor Yellow
        Write-Host '1. In the opened browser tab, select C:\AIAssets\INCLUDE_videos.' -ForegroundColor Yellow
        Write-Host '2. Confirm all six diagnostic clips are matched.' -ForegroundColor Yellow
        Write-Host ('3. Click the button for {0}.' -f $Mode) -ForegroundColor Yellow
        Write-Host 'Keep this PowerShell window open. This pass will continue after 6/6 captures.' -ForegroundColor Yellow

        $deadline = (Get-Date).AddMinutes(20)
        while ((Get-Date) -lt $deadline) {
            $serverProcess.Refresh()
            if ($serverProcess.HasExited) {
                throw ("Phase 7 {0} capture server exited during capture. Inspect {1} and {2}" -f $Mode, $stdoutLog, $stderrLog)
            }
            $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 5
            $receivedPaths = @()
            foreach ($record in @($status.received)) {
                if ($null -ne $record) {
                    $normalizedPath = ([string]$record.video_path).Replace('\', '/').ToLowerInvariant()
                    if ($TargetSet.ContainsKey($normalizedPath)) {
                        $receivedPaths += $normalizedPath
                    }
                }
            }
            $receivedCount = $receivedPaths.Count
            if ($receivedCount -ne $lastCount) {
                Write-Host ('Phase 7 {0} captures received: {1} / 6' -f $Mode, $receivedCount) -ForegroundColor Cyan
                $lastCount = $receivedCount
            }
            if ($receivedCount -eq 6) {
                break
            }
            Start-Sleep -Seconds 2
        }

        if ($lastCount -ne 6) {
            throw ("Timed out waiting for Phase 7 {0} captures. Received {1}/6. Data remains at {2}" -f $Mode, $lastCount, $Destination)
        }
    }
    finally {
        if ($null -ne $serverProcess) {
            $serverProcess.Refresh()
            if (-not $serverProcess.HasExited) {
                Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 300
            }
        }
    }

    $clipDirectory = Join-Path $Destination 'clips'
    $captureFiles = @(Get-ChildItem -LiteralPath $clipDirectory -Filter '*.islbrw' -File)
    if ($captureFiles.Count -ne 6) {
        throw ("Expected 6 Phase 7 {0} capture containers, found {1}. Directory: {2}" -f $Mode, $captureFiles.Count, $Destination)
    }
}

Invoke-Phase7CapturePass -Mode 'current_40ms' -Destination $ModeAPath -Port 8767 -PassName 'Mode A: existing 40 ms gate and synchronous toDataURL'
Invoke-Phase7CapturePass -Mode 'every_presented_frame' -Destination $ModeBPath -Port 8768 -PassName 'Mode B: every presented callback and asynchronous toBlob'

Write-Host ''
Write-Host 'Running FFprobe PTS alignment and capture-completeness analysis...' -ForegroundColor Cyan
$AnalysisLog = Join-Path $LogRoot 'phase7_analysis.log'
$analysisArguments = @(
    '--repo', $RepoPath,
    '--data-root', $DataRoot,
    '--manifest', $ManifestPath,
    '--mode-a-dir', $ModeAPath,
    '--mode-b-dir', $ModeBPath,
    '--output-dir', $OutputPath,
    '--ffprobe', $FfprobePath
)
& $Python '.\scripts\phase7_capture_completeness.py' @analysisArguments *> $AnalysisLog
$analysisExit = $LASTEXITCODE
Get-Content -LiteralPath $AnalysisLog -Tail 30 | ForEach-Object { Write-Host $_ }
if ($analysisExit -ne 0) {
    throw "Phase 7 analysis failed (exit $analysisExit). Full log: $AnalysisLog. Captures retained in $ModeAPath and $ModeBPath"
}

$requiredOutputs = @(
    'phase7_summary.json',
    'PHASE7_CAPTURE_COMPLETENESS_REPORT.md',
    'phase7_clip_comparison.csv',
    'phase7_frame_metrics.csv',
    'phase7_mode_pairwise_comparison.csv',
    'phase7_source_pts_metadata.csv'
)
foreach ($requiredFile in $requiredOutputs) {
    $requiredPath = Join-Path $OutputPath $requiredFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Phase 7 output missing: $requiredFile"
    }
    Copy-Item -LiteralPath $requiredPath -Destination (Join-Path $Stage 'results')
}

$modePairs = @(
    @{ Mode = 'current_40ms'; Path = $ModeAPath },
    @{ Mode = 'every_presented_frame'; Path = $ModeBPath }
)
foreach ($modePair in $modePairs) {
    $modeStage = Join-Path (Join-Path $Stage 'captures') $modePair.Mode
    Copy-Item -LiteralPath (Join-Path $modePair.Path 'capture_index.json') -Destination $modeStage
    $modeClipsStage = Join-Path $modeStage 'clips'
    $modeClipFiles = Get-ChildItem -LiteralPath (Join-Path $modePair.Path 'clips') -Filter '*.islbrw' -File
    foreach ($modeClipFile in $modeClipFiles) {
        Copy-Item -LiteralPath $modeClipFile.FullName -Destination $modeClipsStage
    }
}

$logFiles = Get-ChildItem -LiteralPath $LogRoot -File
foreach ($logFile in $logFiles) {
    Copy-Item -LiteralPath $logFile.FullName -Destination (Join-Path $Stage 'logs')
}
Copy-Item -LiteralPath $RunnerMeta -Destination (Join-Path $Stage 'metadata')

$hashLines = @()
$stageFiles = Get-ChildItem -LiteralPath $Stage -File -Recurse | Sort-Object FullName
foreach ($stageFile in $stageFiles) {
    $fileHash = Get-FileHash -LiteralPath $stageFile.FullName -Algorithm SHA256
    $relativePath = $stageFile.FullName.Substring($Stage.Length + 1).Replace('\', '/')
    $hashLines += ('{0}  {1}' -f $fileHash.Hash, $relativePath)
}
$hashLines | Set-Content -LiteralPath (Join-Path $Stage 'SHA256SUMS.txt') -Encoding UTF8

if (Test-Path -LiteralPath $Bundle) {
    Remove-Item -LiteralPath $Bundle -Force
}
$stageWildcard = Join-Path $Stage '*'
Compress-Archive -Path $stageWildcard -DestinationPath $Bundle -CompressionLevel Optimal

$VerifyLog = Join-Path $LogRoot 'verify_phase7_bundle.log'
$VerifierPath = Join-Path $ProjectRoot 'scripts\verify_phase7_bundle.py'
& $Python $VerifierPath $Bundle *> $VerifyLog
$verifyExit = $LASTEXITCODE
Get-Content -LiteralPath $VerifyLog | ForEach-Object { Write-Host $_ }
if ($verifyExit -ne 0) {
    throw "Phase 7 result bundle failed integrity verification. See $VerifyLog"
}

$bundleHash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash
$ReportPath = Join-Path $OutputPath 'PHASE7_CAPTURE_COMPLETENESS_REPORT.md'

Write-Host ''
Write-Host 'PHASE 7 COMPLETED' -ForegroundColor Green
Write-Host ('RESULT_BUNDLE={0}' -f $Bundle)
Write-Host ('BUNDLE_SHA256={0}' -f $bundleHash)
Write-Host ('REPORT={0}' -f $ReportPath)
Write-Host ('MODE_A_CAPTURE_DIR={0}' -f $ModeAPath)
Write-Host ('MODE_B_CAPTURE_DIR={0}' -f $ModeBPath)
Write-Host ('TEST_LOG={0}' -f $TestLog)
Write-Host 'IoT stayed disabled. Classifier/checkpoint was not loaded. No production code or settings were changed.' -ForegroundColor Green
