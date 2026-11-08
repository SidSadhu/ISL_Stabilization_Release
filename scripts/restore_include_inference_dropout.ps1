[CmdletBinding()]
param([string]$IncludeRepo = 'C:\AIAssets\INCLUDE')
$ErrorActionPreference = 'Stop'
$modelPath = Join-Path $IncludeRepo 'models\transformer.py'
$backupPath = "$modelPath.pre_isl_deterministic_inference.bak"
if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) { throw "Rollback backup not found: $backupPath" }
Copy-Item -LiteralPath $backupPath -Destination $modelPath -Force
Write-Host "Restored original AI4Bharat source from: $backupPath" -ForegroundColor Green
Write-Host 'Restart the ISL app for the restored module to reload.' -ForegroundColor Cyan
