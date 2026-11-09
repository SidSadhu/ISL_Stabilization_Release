$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\siddh\Downloads\ISL_Stabilization_Release\ISL_Stabilization_Release'
if (-not (Test-Path $Root)) { $Root = (Get-Location).Path }
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Js = Join-Path $Root 'src\isl_recognition\static\landmark_diagnostics.js'
$Helper = Join-Path $PSScriptRoot 'apply_overlay_v3_final_fix.py'
if (-not (Test-Path $Python)) { throw "Project Python environment not found: $Python" }
if (-not (Test-Path $Js)) { throw "Project landmark diagnostics source not found: $Js" }
if (-not (Test-Path $Helper)) { throw "Patch helper not found: $Helper" }

Write-Host "Project root: $Root" -ForegroundColor Cyan
Write-Host "`n========== 1. REPAIR CAPTURE-STATE GATING AND TEST ==========" -ForegroundColor Cyan
& $Python $Helper $Root
$PatchExit = $LASTEXITCODE
if ($PatchExit -ne 0) { throw "Repair stopped with exit code $PatchExit. Preserve the output." }

$Node = Get-Command node -ErrorAction SilentlyContinue
if ($Node) {
    Write-Host "`n========== 2. JAVASCRIPT SYNTAX ==========" -ForegroundColor Cyan
    & $Node.Source --check $Js
    $NodeExit = $LASTEXITCODE
    if ($NodeExit -ne 0) { throw 'JavaScript syntax check failed.' }
} else {
    Write-Host 'Node.js not installed; JavaScript syntax check skipped.' -ForegroundColor Yellow
}

Push-Location $Root
try {
    Write-Host "`n========== 3. TARGETED OVERLAY TESTS ==========" -ForegroundColor Cyan
    & $Python -m pytest -q tests\test_live_landmark_capture_overlay.py tests\test_live_landmark_reconnect.py
    $TargetExit = $LASTEXITCODE
    if ($TargetExit -ne 0) { throw 'Targeted tests failed. Preserve the output.' }

    Write-Host "`n========== 4. COMPLETE PROJECT TEST SUITE ==========" -ForegroundColor Cyan
    & $Python -m pytest -q tests
    $FullExit = $LASTEXITCODE
    if ($FullExit -ne 0) { throw 'Complete project test suite failed. Preserve the output.' }
} finally {
    Pop-Location
}
Write-Host "`nFINAL OVERLAY FIX INSTALLED; ALL PROJECT TESTS PASSED." -ForegroundColor Green
Write-Host 'Restart the app and press Ctrl+F5 before live validation.' -ForegroundColor Green
