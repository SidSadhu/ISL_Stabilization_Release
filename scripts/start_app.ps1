param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
if (Test-Path '.\.venv\Scripts\python.exe') { $Python = (Resolve-Path '.\.venv\Scripts\python.exe').Path } else { $Python = (Get-Command python).Source }
if (-not (Test-Path '.\.env')) { Copy-Item '.\.env.example' '.\.env' }
$port = 8000
$portLine = Select-String -Path '.\.env' -Pattern '^ISL_PORT=(\d+)$' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portLine -and $portLine.Matches.Count -gt 0) { $port = [int]$portLine.Matches[0].Groups[1].Value }
# python-dotenv loads the project-root .env before app configuration is instantiated.
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$port" }
& $Python -m uvicorn isl_recognition.webapp:app --app-dir .\src --host 127.0.0.1 --port $port --ws-max-size 4000000
