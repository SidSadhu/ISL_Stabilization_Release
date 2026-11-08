[CmdletBinding()]
param([string]$IncludeRepo = 'C:\AIAssets\INCLUDE')
$ErrorActionPreference = 'Stop'
$modelPath = Join-Path $IncludeRepo 'models\transformer.py'
if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) { throw "AI4Bharat transformer source not found: $modelPath" }
$source = [System.IO.File]::ReadAllText($modelPath)
$fixedPattern = 'F\.dropout\s*\(\s*x\s*,\s*p\s*=\s*0\.2\s*,\s*training\s*=\s*self\.training\s*\)'
$unfixedPattern = 'F\.dropout\s*\(\s*x\s*,\s*p\s*=\s*0\.2\s*\)'
if ([regex]::IsMatch($source, $fixedPattern)) { Write-Host 'Already fixed: dropout respects model.training.' -ForegroundColor Green; exit 0 }
$matches = [regex]::Matches($source, $unfixedPattern)
if ($matches.Count -ne 1) { throw "Expected exactly one unconditional F.dropout(x, p=0.2) call, found $($matches.Count). No file was changed." }
$backupPath = "$modelPath.pre_isl_deterministic_inference.bak"
if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
    Copy-Item -LiteralPath $modelPath -Destination $backupPath
    Write-Host "Original source backed up to: $backupPath" -ForegroundColor Yellow
} else {
    $backupText = [System.IO.File]::ReadAllText($backupPath)
    if (-not [regex]::IsMatch($backupText, $unfixedPattern)) { throw "Existing backup does not contain expected original call: $backupPath. No file was changed." }
    Write-Host "Preserving existing backup: $backupPath" -ForegroundColor Yellow
}
$replacement = 'F.dropout(x, p=0.2, training=self.training)'
$updated = [regex]::Replace($source, $unfixedPattern, $replacement, 1)
if ($updated -eq $source -or -not [regex]::IsMatch($updated, $fixedPattern)) { throw 'Patch verification failed; source was not written.' }
[System.IO.File]::WriteAllText($modelPath, $updated, [System.Text.UTF8Encoding]::new($false))
$verified = [System.IO.File]::ReadAllText($modelPath)
if (-not [regex]::IsMatch($verified, $fixedPattern) -or [regex]::IsMatch($verified, $unfixedPattern)) { throw 'Post-write validation failed. Restore from backup before proceeding.' }
Write-Host 'Fixed inference dropout: F.dropout now follows self.training.' -ForegroundColor Green
Write-Host "Updated file: $modelPath"
Write-Host "Rollback backup: $backupPath"
Write-Host 'Restart the ISL app before testing. Checkpoint weights were not changed.' -ForegroundColor Cyan
