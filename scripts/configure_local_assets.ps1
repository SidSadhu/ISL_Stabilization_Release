param(
  [string]$RepoPath = 'C:\AIAssets\INCLUDE',
  [string]$CheckpointPath = 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth',
  [string]$VideoRoot = 'C:\AIAssets\INCLUDE_videos',
  [string]$MetadataPath = ''
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
if (-not $MetadataPath) { $MetadataPath = Join-Path $ProjectRoot 'data\include50_video_metadata.json' }
if (-not (Test-Path '.\.env')) { Copy-Item '.\.env.example' '.\.env' }
$envPath = Join-Path $ProjectRoot '.env'
$backup = "$envPath.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $envPath $backup
$lines = @(Get-Content $envPath)
$values = [ordered]@{
  AI4BHARAT_REPO = $RepoPath.Replace('\','/')
  ISL_CHECKPOINT = $CheckpointPath.Replace('\','/')
  ISL_REFERENCE_VIDEO_DIR = $VideoRoot.Replace('\','/')
  ISL_REFERENCE_METADATA = $MetadataPath.Replace('\','/')
  ISL_HOST = '127.0.0.1'
  ISL_PORT = '8000'
  ISL_MAX_CAPTURE_SECONDS = '6.0'
  ISL_CONFIDENCE_THRESHOLD = '0.40'
  ISL_IOT_ENABLED = 'false'
  ISL_IOT_LABEL_MAP = 'config/iot_label_map.json'
  ISL_IOT_MIN_CONFIDENCE = '0.70'
}
foreach ($key in $values.Keys) {
  $pattern = '^\s*' + [regex]::Escape($key) + '\s*=.*$'
  $updated = $false
  $next = New-Object System.Collections.Generic.List[string]
  foreach ($line in $lines) {
    if ($line -match $pattern) {
      if (-not $updated) { $next.Add("$key=$($values[$key])"); $updated = $true }
    } else { $next.Add($line) }
  }
  if (-not $updated) { $next.Add("$key=$($values[$key])") }
  $lines = @($next.ToArray())
}
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($envPath, [string[]]$lines, $utf8NoBom)
Write-Host "Updated $envPath (backup: $backup)" -ForegroundColor Green
foreach ($pair in @(@('AI4BHARAT_REPO',$RepoPath),@('ISL_CHECKPOINT',$CheckpointPath),@('ISL_REFERENCE_VIDEO_DIR',$VideoRoot),@('ISL_REFERENCE_METADATA',$MetadataPath))) {
  if (Test-Path $pair[1]) { Write-Host "OK   $($pair[0]): $($pair[1])" -ForegroundColor Green }
  else { Write-Host "WARN $($pair[0]) path not found yet: $($pair[1])" -ForegroundColor Yellow }
}
Write-Host 'Safety setting enforced in application code: pre-IoT lockdown cannot be disabled from .env. ISL_IOT_ENABLED=false is written for clarity.' -ForegroundColor Cyan
Write-Host 'Restart the app after editing .env; environment settings are read at process startup.'
