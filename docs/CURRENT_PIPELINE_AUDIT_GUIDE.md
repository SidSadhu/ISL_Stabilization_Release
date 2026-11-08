# Historical ISL pipeline A/B audit — Q72 baseline (read-only)

This document describes a historical Q72 diagnostic. It is retained as experiment history and must not be interpreted as the current production capture path. The audited RC3 client uses 640px Canvas width and JPEG quality 0.92. The Q72 path below is superseded for production-equivalent diagnostics.

This diagnostic does not patch application code, install packages, edit the AI4Bharat checkout, change the checkpoint, change `.env`, or alter IoT settings.

It compares two runs of the currently installed MediaPipe version on the same source video frames:

1. Original decoded frame -> BGR to RGB -> current MediaPipe extraction (no JPEG round-trip).
2. Historical 640-pixel resize -> JPEG quality 72 -> the project `MediaPipeFeatureExtractor` (not the current production transform).

It also compares the current deterministic final-layer dropout behaviour with the original AI4Bharat final-layer `F.dropout(x, p=0.2)` behaviour, which remains active even after `.eval()` because upstream omits the `training` argument.

**Important limitation:** it does NOT reproduce the historical MediaPipe `upper_body_only=True` model. It uses the currently installed MediaPipe in both image paths. The report marks historical pose equivalence as NOT TESTED. A separate, verified legacy MediaPipe environment or a faithful port of its original pose graph/model assets is required for that comparison.

## Run on Windows

From the existing project root, set the paths in the same PowerShell session:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location).Path "src")
$env:AI4BHARAT_REPO = "C:\AIAssets\INCLUDE"
$env:ISL_CHECKPOINT = "C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth"
$env:ISL_LABEL_MAP = (Resolve-Path ".\config\label_map_include50.json").Path
$env:ISL_CONFIDENCE_THRESHOLD = "0.40"
```

This historical script remains useful only for reproducing the old Q72 exploratory A/B. For the consolidated Phase 1 benchmark, use `scripts/serve_browser_validation_capture.py` followed by `scripts/evaluate_browser_canvas_validation.py`; those tools capture actual browser Canvas JPEG 0.92 bytes and require the exact official 77-item manifest.

Run the historical Q72 diagnostic only when reproducing the archived experiment:

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_current_pipeline_ab.py `
  --manifest outputs\include50_local_manifest.csv `
  --data-root "C:\AIAssets\INCLUDE_videos" `
  --split val `
  --output outputs\diagnostics\current_pipeline_ab_val.json
```

This processes the available validation videos (expected to be a small mapped-sign subset in the current local manifest). It reports coverage, frame-level feature differences, top-1/top-5, and dropout-mode variation for each video.

To diagnose a broader set, use a larger manifest/split or remove `--max-samples` (the default is all selected rows). Keep the held-out test split locked until the pipeline and confidence policy are frozen. The final one-time test requires the explicit `--split test --allow-test-evaluation` flags; never use it for tuning.

## Interpret carefully

- If raw decoded frames work but the 640/JPEG72 path fails, the camera image transform is a contributing cause.
- If deterministic and upstream-dropout modes differ materially, the current dropout correction changes inference results and must be selected using validation data.
- If both current-version paths fail on official validation clips, the next investigation is the historical MediaPipe pose/hand model and model-library provenance.
- None of these outputs alone proves exact historical feature parity or user-webcam accuracy.
