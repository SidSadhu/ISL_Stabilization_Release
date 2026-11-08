# Phase 2 — 77-Clip Feature Provenance and Preprocessing Ablation

## Objective

Run a single resumable, evidence-led all-77 comparison to isolate the current application's native-vs-resized frame path, JPEG quality-0.92 transport effect, and current-application-vs-upstream-modern-adapter extraction effect.

## Locked conditions

- INCLUDE-50 official validation split only: 77 clips / 50 classes.
- AI4Bharat HEAD `df348f4a640060d4d39f02556d9f96851e8c46c7`.
- Checkpoint SHA-256 `8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4`.
- Python 3.12 x64, MediaPipe 0.10.21, OpenCV 4.11.0, PyTorch 2.11.0.
- 50 classes, 134-D features, 169-frame model input, x×1920 / y×1080 preprocessing.
- IoT `false`; empty IoT map; `pre_iot_lockdown=True`.
- Text threshold recorded as 0.40 for diagnostics only; no calibration or production change.
- No train/test split, no model training, no checkpoint changes, no MQTT/ESP32 calls.
- JPEG quality 72 is excluded; current production quality 92 is the only JPEG condition.

## Four compared paths

| Path | Frames and transform | Purpose |
|---|---|---|
| `app_direct_native` | Current application mapper on original OpenCV-decoded source frames | Baseline current application extractor without additional resize/encode. |
| `resized_640_png` | Same mapper on OpenCV INTER_LINEAR 640 px resize + lossless PNG encode/decode | Approximate resize-only effect. |
| `resized_640_jpeg_q92` | Same mapper on identical resized frame + JPEG q92 encode/decode | Approximate production quality-92 transport effect. |
| `upstream_original` | Pinned upstream generator on original source video, with modern full-Pose/first-25 adapter if legacy API is absent | Current implementation vs upstream source behavior under the same modern runtime, not historical detector parity. |

All variants use the same decoded source frame order and sequential MediaPipe sessions. The OpenCV resize is not pixel-identical to Chrome Canvas `drawImage`. Actual browser Canvas evidence remains the Phase 1 paired benchmark.

## Rebuild the derived Phase 1 report (optional)

The original Windows metrics and per-clip CSV are retained in `engineering/phase1_evidence/`. Rebuild the derived report bundle with:

```powershell
$Python = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Project Python 3.12 environment missing.' }
& $Python .\scripts\build_phase2_phase1_report.py `
    --metrics .\engineering\phase1_evidence\metrics.json `
    --per-clip .\engineering\phase1_evidence\per_clip.csv `
    --out-dir .\engineering\phase2_evidence
if ($LASTEXITCODE -ne 0) { throw 'Phase 1 evidence report rebuild failed.' }
```


## Target-Windows 76/77 failure recovery

The first 7 November 2026 Windows run failed only for `Adjectives/79. short/MVI_9302.MOV`, because upstream `process_pose_keypoints()` dereferenced a missing Pose result. The diagnostic applies a local `None`-Pose-to-25-NaN guard, leaving the upstream checkout untouched.

To retain the 76 proven successful clip computations, run the runner with **both** `--resume` and `--resume-compatible-missing-pose-guard`. This compatibility option is intentionally narrow: only the exact previous diagnostic/runner hashes from the supplied 76/77 run are accepted, and all other asset, runtime, repository, application-source, label, checkpoint, manifest and per-video hashes must match. Only successful cached records are eligible; failed/missing records are rerun. Do not use `--force`.

After the recovery, acceptance requires `selected_count=77`, `successful_count=77`, `failed_count=0`, `status=completed`, and successful report generation.

## One-block Windows command

Run from the extracted stabilization project root. The block checks the active Python version and verifies the assets and IoT environment. It writes a full log to disk so console scrollback truncation is not a blocker. `--resume` reuses only per-clip results whose fingerprint matches the current script/source/runtime/video hashes; failed or stale clips are rerun.

```powershell
$ErrorActionPreference = 'Stop'
$Root = (Get-Location).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Repo = 'C:\AIAssets\INCLUDE'
$Checkpoint = 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth'
$VideoRoot = 'C:\AIAssets\INCLUDE_videos'
$Manifest = Join-Path $Root 'outputs\include50_official_val_manifest.csv'
$OutDir = Join-Path $Root 'outputs\phase2_feature_provenance_77'
$LogDir = Join-Path $OutDir 'logs'
$Log = Join-Path $OutDir 'phase2_run.log'

foreach ($p in @($Python,$Repo,$Checkpoint,$VideoRoot,$Manifest)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Missing required path: $p" }
}

$env:AI4BHARAT_REPO = $Repo
$env:ISL_CHECKPOINT = $Checkpoint
$env:ISL_REFERENCE_VIDEO_DIR = $VideoRoot
$env:ISL_LABEL_MAP = 'config/label_map_include50.json'
$env:ISL_IOT_LABEL_MAP = 'config/iot_label_map.json'
$env:ISL_IOT_ENABLED = 'false'
$env:ISL_CONFIDENCE_THRESHOLD = '0.40'
$env:ISL_TORCH_NUM_THREADS = '1'

$ExpectedCheckpoint = '8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4'
$ActualCheckpoint = (Get-FileHash -LiteralPath $Checkpoint -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualCheckpoint -ne $ExpectedCheckpoint) { throw 'Checkpoint SHA-256 mismatch.' }

$ExpectedCommit = 'df348f4a640060d4d39f02556d9f96851e8c46c7'
$ActualCommit = (& git -C $Repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $ActualCommit -ne $ExpectedCommit) { throw 'Pinned AI4Bharat commit mismatch.' }

$IoT = Get-Content '.\config\iot_label_map.json' -Raw | ConvertFrom-Json
if (@($IoT.mappings.PSObject.Properties).Count -ne 0) { throw 'IoT mapping must be empty.' }
if (Select-String -Path '.\.env' -Pattern '^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes)\s*$' -Quiet) { throw 'IoT enabled in .env.' }

New-Item -ItemType Directory -Force -Path $OutDir,$LogDir | Out-Null

Write-Host 'Starting resumable 77-clip Phase 2 feature provenance run.' -ForegroundColor Cyan
Write-Host "Full output log: $Log"
Write-Host 'This does not use or modify the test split, checkpoint, model contract, or IoT.'

& $Python '.\scripts\run_phase2_feature_provenance_77.py' `
    --manifest $Manifest `
    --repo $Repo `
    --checkpoint $Checkpoint `
    --data-root $VideoRoot `
    --out-dir $OutDir `
    --resume `
    --resume-compatible-missing-pose-guard 2>&1 | Tee-Object -FilePath $Log

if ($LASTEXITCODE -ne 0) {
    throw "Phase 2 runner incomplete/failed. Preserve outputs and inspect: $Log"
}

$Summary = Join-Path $OutDir 'summary.json'
$PerClip = Join-Path $OutDir 'per_clip_summary.csv'
if (-not (Test-Path $Summary) -or -not (Test-Path $PerClip)) { throw 'Phase 2 summary outputs are missing.' }

$ReportDir = Join-Path $OutDir 'report'
& $Python '.\scripts\build_phase2_ablation_report.py' `
    --summary $Summary `
    --per-clip $PerClip `
    --out-dir $ReportDir
if ($LASTEXITCODE -ne 0) { throw 'Phase 2 human-readable report generation failed.' }

$S = Get-Content $Summary -Raw | ConvertFrom-Json
if ($S.status -ne 'completed' -or $S.successful_count -ne 77 -or $S.failed_count -ne 0) {
    throw 'Phase 2 did not complete all 77 diagnostics. Use --resume after inspecting the logs.'
}

Write-Host "Phase 2 completed: $($S.successful_count)/77" -ForegroundColor Green
Write-Host "Summary: $Summary"
Write-Host "Per-clip table: $PerClip"
Write-Host "Per-clip JSON: $(Join-Path $OutDir 'per_clip')"
Write-Host "Per-clip logs: $(Join-Path $OutDir 'logs')"
Write-Host "Human-readable report: $(Join-Path $ReportDir 'PHASE2_ABLATION_REPORT.md')"
Write-Host "Report figures and tables: $ReportDir"
```

## Console truncation / interruption

The terminal is not the record of truth. The durable records are `summary.json`, `per_clip_summary.csv`, one JSON report and one log per clip. If execution stops, do not delete output files; run the same command again. The runner will resume only fingerprint-matching successful clips and retry incomplete/failed clips. Do not use `--force` unless a deliberate full recomputation is authorised.

## Required interpretation

- A large `app_direct_native` → `resized_640_png` difference implicates resize/resolution sensitivity.
- A large `resized_640_png` → `resized_640_jpeg_q92` difference implicates JPEG q92 encode/decode effects.
- A large `app_direct_native` → `upstream_original` difference under the same modern runtime points to mapping/session/configuration or implementation drift; inspect per-feature presence and top dimensions.
- A large q92 → upstream difference combines resize/JPEG and implementation differences; interpret it only after the two earlier contrasts.
- None of these tests proves historical MediaPipe `upper_body_only=True` detector parity.

Do not select a production fix based on one clip. Require all 77 clips, aggregate distributions, per-class/pair outcomes, and regression evidence. Do not change the production threshold or test split during this phase.
