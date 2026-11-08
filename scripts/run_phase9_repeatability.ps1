[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepoPath,
    [Parameter(Mandatory=$true)][string]$DataRoot,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$FfprobePath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Get-Location).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Runner = Join-Path $ProjectRoot 'scripts\phase9_orchestrator.py'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw ('Project Python missing: {0}' -f $Python)
}
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw ('Phase 9 Python orchestrator missing: {0}' -f $Runner)
}

$RunnerArgs = @(
    $Runner,
    '--repo', $RepoPath,
    '--data-root', $DataRoot,
    '--manifest', $Manifest,
    '--ffprobe', $FfprobePath
)

& $Python @RunnerArgs
$RunnerExit = $LASTEXITCODE
if ($RunnerExit -ne 0) {
    throw ('Phase 9 workflow failed with exit code {0}.' -f $RunnerExit)
}
