[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepoPath,
    [Parameter(Mandatory=$true)][string]$DataRoot,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$FfprobePath,
    [Parameter(Mandatory=$false)][string]$ResumeForegroundDir = '',
    [Parameter(Mandatory=$false)][string]$ResumeVisibleUnfocusedDir = ''
)
# Deliberately small Windows PowerShell 5.1 wrapper. Python owns orchestration.
$ErrorActionPreference = 'Continue'
$ProjectRoot = (Get-Location).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Runner = Join-Path $ProjectRoot 'scripts\phase10b_browser_lifecycle_orchestrator.py'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Error ('Project Python missing: {0}' -f $Python)
    $global:LASTEXITCODE = 2
    return
}
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    Write-Error ('Phase 10B orchestrator missing: {0}' -f $Runner)
    $global:LASTEXITCODE = 2
    return
}
$RunnerArgs = @($Runner, '--repo', $RepoPath, '--data-root', $DataRoot, '--manifest', $Manifest, '--ffprobe', $FfprobePath)
if (-not [string]::IsNullOrWhiteSpace($ResumeForegroundDir)) {
    $RunnerArgs += @('--resume-foreground-dir', $ResumeForegroundDir)
}
if (-not [string]::IsNullOrWhiteSpace($ResumeVisibleUnfocusedDir)) {
    $RunnerArgs += @('--resume-visible-unfocused-dir', $ResumeVisibleUnfocusedDir)
}
& $Python @RunnerArgs
$RunnerExit = $LASTEXITCODE
if ($RunnerExit -ne 0) {
    Write-Error ('Phase 10B workflow failed with exit code {0}.' -f $RunnerExit)
    $global:LASTEXITCODE = $RunnerExit
    return
}
$global:LASTEXITCODE = 0
return
