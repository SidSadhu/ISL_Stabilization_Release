param(
  [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$ProjectName = Split-Path $ProjectRoot -Leaf
$Parent = Split-Path $ProjectRoot -Parent
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupRoot = Join-Path $Parent ($ProjectName + '-backup-before-isl-work-' + $Stamp)

Write-Host "Creating a source backup: $BackupRoot" -ForegroundColor Cyan
$robocopyArgs = @(
  $ProjectRoot, $BackupRoot,
  '/E', '/COPY:DAT', '/R:1', '/W:1', '/NP',
  '/XD', (Join-Path $ProjectRoot '.venv'), (Join-Path $ProjectRoot '.git'),
          (Join-Path $ProjectRoot '__pycache__'), (Join-Path $ProjectRoot '.pytest_cache'),
          (Join-Path $ProjectRoot '.mypy_cache'), (Join-Path $ProjectRoot '.ruff_cache'),
  '/XF', '*.pyc', '*.pyo'
)
& robocopy @robocopyArgs
$CopyCode = $LASTEXITCODE
if ($CopyCode -ge 8) { throw "Robocopy failed with exit code $CopyCode. Do not continue until the backup is repaired." }
if (-not (Test-Path $BackupRoot)) { throw 'Backup directory was not created.' }
Write-Host "Backup complete (robocopy exit $CopyCode). The active .venv was intentionally not copied." -ForegroundColor Green

$Git = Get-Command git -ErrorAction SilentlyContinue
if (-not $Git) {
  Write-Warning 'Git is not installed/on PATH. Backup is complete; install Git before making code changes.'
  return
}

$InsideRepo = $false
try {
  & $Git.Source -C $ProjectRoot rev-parse --is-inside-work-tree *> $null
  $InsideRepo = ($LASTEXITCODE -eq 0)
} catch { $InsideRepo = $false }
if (-not $InsideRepo) {
  Write-Host 'Initialising a local Git repository (no remote will be added).' -ForegroundColor Cyan
  & $Git.Source -C $ProjectRoot init
  if ($LASTEXITCODE -ne 0) { throw 'git init failed.' }
} else {
  Write-Host 'Existing Git repository detected; preserving its history and remotes.' -ForegroundColor Green
}

$GitIgnorePath = Join-Path $ProjectRoot '.gitignore'
$IgnoreLines = @(
  '# Local secrets and settings', '.env', '.env.*', '!.env.example',
  '# Virtual environments and caches', '.venv/', 'venv/', '__pycache__/', '*.py[cod]', '.pytest_cache/', '.mypy_cache/', '.ruff_cache/',
  '# Model/data assets and generated runtime state', '*.pth', '*.pt', '*.ckpt', '*.mov', '*.mp4', '*.avi', '*.m4v', '*.webm', 'checkpoints/', 'data/include_videos/', 'outputs/', 'var/', '*.sqlite3', '*.db',
  '# IDE and OS files', '.idea/', '.vscode/', 'Thumbs.db', 'Desktop.ini'
)
$Existing = @()
if (Test-Path $GitIgnorePath) { $Existing = @(Get-Content $GitIgnorePath) }
$ToAppend = @($IgnoreLines | Where-Object { $_ -notin $Existing })
if ($ToAppend.Count -gt 0) {
  Add-Content -Path $GitIgnorePath -Value $ToAppend -Encoding UTF8
}

# .gitignore does not protect secrets that were already tracked. Remove local env files
# from the index (not from disk) before staging the baseline. Keep the shareable template.
$TrackedEnvFiles = @(& $Git.Source -C $ProjectRoot ls-files -- '.env' '.env.*')
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect tracked environment files.' }
foreach ($TrackedEnv in $TrackedEnvFiles) {
  if ($TrackedEnv -and $TrackedEnv -ne '.env.example') {
    & $Git.Source -C $ProjectRoot rm --cached --ignore-unmatch -- $TrackedEnv
    if ($LASTEXITCODE -ne 0) { throw "Could not untrack local environment file: $TrackedEnv" }
  }
}

& $Git.Source -C $ProjectRoot add --all
if ($LASTEXITCODE -ne 0) { throw 'git add failed. Inspect .gitignore and file status before proceeding.' }
Write-Host 'Git baseline staged. No commit or remote was created; review status, then commit when satisfied.' -ForegroundColor Green
& $Git.Source -C $ProjectRoot status --short
Write-Host "Backup: $BackupRoot" -ForegroundColor Cyan
