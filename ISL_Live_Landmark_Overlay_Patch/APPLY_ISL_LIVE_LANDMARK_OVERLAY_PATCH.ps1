$ErrorActionPreference = 'Stop'

function Find-ProjectRoot {
    $candidates = @((Get-Location).Path, $PSScriptRoot)
    foreach ($candidate in $candidates) {
        $current = [System.IO.DirectoryInfo]::new($candidate)
        while ($null -ne $current) {
            $probe = Join-Path $current.FullName 'src\isl_recognition\static\landmark_diagnostics.js'
            $venv = Join-Path $current.FullName '.venv\Scripts\python.exe'
            if ((Test-Path $probe) -and (Test-Path $venv)) { return $current.FullName }
            $current = $current.Parent
        }
    }
    return $null
}

$Root = Find-ProjectRoot
if (-not $Root) {
    throw 'Could not locate the ISL project root. Run this script from the project root (the folder containing src and .venv).'
}
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$PatchPy = Join-Path $PSScriptRoot 'apply_overlay_patch.py'
if (-not (Test-Path $PatchPy)) { throw "Patch helper not found: $PatchPy" }

Write-Host "Project root: $Root" -ForegroundColor Cyan
Write-Host "`n========== 1. APPLY LIVE OVERLAY PATCH ==========" -ForegroundColor Cyan
& $Python $PatchPy $Root
$PatchExit = $LASTEXITCODE
if ($PatchExit -ne 0) { throw "Patch failed with exit code $PatchExit. Preserve the output." }

$NodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($NodeCommand) {
    Write-Host "`n========== 2. CHECK JAVASCRIPT SYNTAX ==========" -ForegroundColor Cyan
    & $NodeCommand.Source --check (Join-Path $Root 'src\isl_recognition\static\landmark_diagnostics.js')
    if ($LASTEXITCODE -ne 0) { throw 'JavaScript syntax check failed.' }
} else {
    Write-Host 'Node.js not found; skipping optional JavaScript parser check.' -ForegroundColor Yellow
}

Write-Host "`n========== 3. RUN OVERLAY REGRESSION TESTS ==========" -ForegroundColor Cyan
Push-Location $Root
try {
    & $Python -m pytest -q tests\test_live_landmark_capture_overlay.py
    $TargetExit = $LASTEXITCODE
    if ($TargetExit -ne 0) { throw 'Overlay regression tests failed.' }

    Write-Host "`n========== 4. RUN COMPLETE PROJECT TEST SUITE ==========" -ForegroundColor Cyan
    & $Python -m pytest -q tests
    $FullExit = $LASTEXITCODE
    if ($FullExit -ne 0) { throw 'Complete project test suite failed. Preserve the full pytest output.' }
} finally {
    Pop-Location
}

Write-Host "`nLIVE LANDMARK OVERLAY PATCH INSTALLED; ALL PROJECT TESTS PASSED." -ForegroundColor Green
Write-Host 'Restart the app, then press Ctrl+F5 in the browser.' -ForegroundColor Green
