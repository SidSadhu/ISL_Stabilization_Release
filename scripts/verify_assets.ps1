<#
.SYNOPSIS
    Verify external model assets required to run the ISL Recognition application.
    Run this before starting the app or reporting bugs related to model loading.
#>
param(
    [string]$Repo = $env:AI4BHARAT_REPO,
    [string]$Checkpoint = $env:ISL_CHECKPOINT
)

# Load .env if present
$envFile = Join-Path -Path $PSScriptRoot -ChildPath "..\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[^#]' } | ForEach-Object {
        $parts = $_ -split '=', 2
        if ($parts.Length -eq 2 -and -not [System.Environment]::GetEnvironmentVariable($parts[0].Trim())) {
            [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
        }
    }
    if (-not $Repo)       { $Repo       = $env:AI4BHARAT_REPO }
    if (-not $Checkpoint) { $Checkpoint = $env:ISL_CHECKPOINT }
}

$ok = $true

Write-Host ""
Write-Host "=== ISL Asset Verification ===" -ForegroundColor Cyan
Write-Host ""

# Check AI4Bharat repo
if (-not $Repo) {
    Write-Host "FAIL: AI4BHARAT_REPO not set. Set in .env or environment." -ForegroundColor Red
    $ok = $false
} elseif (-not (Test-Path $Repo -PathType Container)) {
    Write-Host "FAIL: AI4Bharat repo not found: $Repo" -ForegroundColor Red
    $ok = $false
} else {
    Write-Host "OK:   AI4Bharat repo found: $Repo" -ForegroundColor Green
    # Check transformer.py exists
    $tf = Join-Path -Path $Repo -ChildPath "models\transformer.py"
    if (Test-Path $tf) {
        Write-Host "OK:   models/transformer.py found" -ForegroundColor Green
        # Check for dropout fix
        $content = Get-Content $tf -Raw
        if ($content -match "training=self\.training") {
            Write-Host "OK:   Deterministic dropout fix is applied" -ForegroundColor Green
        } else {
            Write-Host "WARN: Dropout fix may not be applied. Run scripts\fix_include_inference_dropout.ps1" -ForegroundColor Yellow
        }
    } else {
        Write-Host "WARN: models/transformer.py not found at expected location" -ForegroundColor Yellow
    }
}

# Check checkpoint
if (-not $Checkpoint) {
    Write-Host "FAIL: ISL_CHECKPOINT not set. Set in .env or environment." -ForegroundColor Red
    $ok = $false
} elseif (-not (Test-Path $Checkpoint -PathType Leaf)) {
    Write-Host "FAIL: Checkpoint not found: $Checkpoint" -ForegroundColor Red
    Write-Host "      Download from: https://github.com/AI4Bharat/INCLUDE (pretrained_links.json)" -ForegroundColor Yellow
    $ok = $false
} else {
    $size = (Get-Item $Checkpoint).Length
    Write-Host "OK:   Checkpoint found: $Checkpoint ($([math]::Round($size/1MB,1)) MB)" -ForegroundColor Green
    if ($size -lt 1000000) {
        Write-Host "WARN: Checkpoint is very small ($size bytes); may be a dummy or corrupt file" -ForegroundColor Yellow
    }
}

# Check label map
$labelMap = Join-Path -Path $PSScriptRoot -ChildPath "..\config\label_map_include50.json"
if (Test-Path $labelMap) {
    $mapSize = (Get-Item $labelMap).Length
    Write-Host "OK:   Bundled label map found ($mapSize bytes)" -ForegroundColor Green
} else {
    Write-Host "FAIL: Bundled label map missing at config/label_map_include50.json" -ForegroundColor Red
    $ok = $false
}

# Python environment check
Write-Host ""
Write-Host "=== Python Environment ===" -ForegroundColor Cyan
try {
    $pyVer = python --version 2>&1
    Write-Host "OK:   $pyVer" -ForegroundColor Green
} catch {
    Write-Host "FAIL: python not found in PATH" -ForegroundColor Red; $ok = $false
}

$packages = @("mediapipe", "cv2", "torch", "fastapi", "numpy", "pandas")
foreach ($pkg in $packages) {
    $result = python -c "import $pkg; print($pkg.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK:   $pkg $result" -ForegroundColor Green
    } else {
        Write-Host "FAIL: $pkg not importable" -ForegroundColor Red; $ok = $false
    }
}

Write-Host ""
if ($ok) {
    Write-Host "=== All checks PASSED. Ready to run. ===" -ForegroundColor Green
} else {
    Write-Host "=== Some checks FAILED. See above. ===" -ForegroundColor Red
    exit 1
}
