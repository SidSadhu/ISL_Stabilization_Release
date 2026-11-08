param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$VideoRoot = 'C:\AIAssets\INCLUDE_videos',
  [string]$Manifest = '.\outputs\include50_official_val_manifest.csv',
  [string]$OutputDir = '.\outputs\include50_validation_baseline'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonPath)) { throw 'Existing .venv Python not found. Activate/use the existing Python 3.12 environment; do not rebuild it automatically.' }
$Python = (Resolve-Path $PythonPath).Path

Write-Host '1/3 - Verify and write the exact pinned 77-item official validation manifest' -ForegroundColor Cyan
& $Python .\scripts\prepare_official_validation_manifest.py --repo $RepoPath --output $Manifest
if ($LASTEXITCODE -ne 0) { throw 'Official validation manifest preparation failed. Check the pinned Git commit and split-list hash.' }

Write-Host '2/3 - Download only missing official validation clips (no test samples)' -ForegroundColor Cyan
& $Python .\scripts\download_include_reference_clips.py --profile validation-all --ai4bharat-repo $RepoPath --destination $VideoRoot --download
if ($LASTEXITCODE -ne 0) { throw 'Official validation clip download workflow failed.' }

Write-Host '3/3 - Evaluate the full official validation split' -ForegroundColor Cyan
& $Python .\scripts\evaluate_dataset.py --manifest $Manifest --data-root $VideoRoot --split val --out-dir $OutputDir
if ($LASTEXITCODE -ne 0) { throw 'Validation evaluation was blocked or failed. Inspect metrics.json and console output.' }
Write-Host "Review $OutputDir\metrics.json. Report a complete validation result only when evaluation_complete_for_official_split is true." -ForegroundColor Green
