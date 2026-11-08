<#
.SYNOPSIS
    Run the full ISL test suite with verbose output and a pass/fail summary.
    Core tests (no MediaPipe/PyTorch) always run.
    Model/checkpoint tests are auto-skipped if assets are not configured.
#>
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$ErrorActionPreference = "Stop"
$Root = (Get-Item "$PSScriptRoot\..").FullName
Set-Location $Root

# Activate .venv if present
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $env:PATH = "$Root\.venv\Scripts;$env:PATH"
    Write-Host "Using .venv: $venvPy" -ForegroundColor Cyan
} else {
    Write-Host "No .venv found — using system Python" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== ISL Recognition Test Suite ===" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host ""

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $Root "test_output_$timestamp.txt"

python -m pytest tests\ -v --tb=short --no-header 2>&1 | Tee-Object $logFile
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "=== ALL TESTS PASSED ===" -ForegroundColor Green
} else {
    Write-Host "=== SOME TESTS FAILED (exit code $exitCode) ===" -ForegroundColor Red
}
Write-Host "Log saved to: $logFile"
exit $exitCode
