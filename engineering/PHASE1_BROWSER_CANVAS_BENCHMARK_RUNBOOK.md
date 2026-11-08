# Phase 1 — Browser-native Canvas Validation Runbook

**Status:** Tooling implemented; execution awaits external assets and target Windows Python 3.12.  
**Safety:** Diagnostic-only, loopback receiver, no application/MQTT/broker/simulator/ESP32 connection. IoT must remain disabled.

## 1. Purpose

Measure the currently deployed browser image path on the exact official 77-item INCLUDE-50 validation split. This is deliberately distinct from the old eight-clip pilot and from the older Q72 sweep.

The browser path records actual Chrome/Edge bytes emitted by:

1. local validation video playback at 1×;
2. a 40 ms interval timer with duplicate `video.currentTime` suppression;
3. a 640 px canvas width with proportional height;
4. `canvas.toDataURL('image/jpeg', 0.92)`;
5. no input mirroring;
6. at most 6 seconds and 169 frames per clip.

The paired reference is OpenCV-native frame decoding at timestamps sampled by the browser page. It is a useful reference path, but it is not a claim of pixel-identical Chrome decoding.

## 2. Required local assets

Confirm these paths exist before running:

- `C:\AIAssets\INCLUDE` — complete AI4Bharat Git clone, HEAD exactly `df348f4a640060d4d39f02556d9f96851e8c46c7`.
- `C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth` — SHA-256 exactly `8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4`.
- `C:\AIAssets\INCLUDE_videos` — all 77 original official validation videos, with relative paths matching the official split list (alternate extensions may be resolved by stem by the browser page).
- Project `.venv` created with 64-bit Python 3.12 and dependencies from `requirements-web.txt`, `requirements-mediapipe.txt`, `requirements-model.txt`, `requirements-evaluation.txt`, and `requirements-dev.txt`.

The pinned source tree must contain the one documented patch in `models/transformer.py`: `F.dropout(x, p=0.2, training=self.training)`. The evaluator checks the pinned commit and exact one-line worktree diff. Do not repeat the completed 20-seed experiment or modify checkpoint weights.

## 3. Safety preconditions

Before starting:

- Confirm `.env` contains `ISL_IOT_ENABLED=false` or does not set it (default false).
- Confirm `config/iot_label_map.json` has an empty `mappings` object.
- Confirm app source still has `pre_iot_lockdown: bool = True`.
- Do not start the application, MQTT broker, simulator or connect the ESP32 for this diagnostic.
- Do not unlock IoT to test recognition. The evaluator independently refuses to run if IoT is enabled or mappings are non-empty.

The capture server binds only to `127.0.0.1`. Keep it local; do not expose or port-forward it.

## 4. Generate and verify the pinned manifest

From PowerShell at the extracted project root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\python.exe .\scripts\prepare_official_validation_manifest.py `
  --repo C:\AIAssets\INCLUDE `
  --output .\outputs\include50_official_val_manifest.csv
```

The command must report 77 official validation entries and the expected pinned commit/list hash. It fails closed if Git HEAD, split-list SHA, count, duplicates or labels differ. It does not download videos.

Download only missing official validation clips when needed (never use the test split):

```powershell
.\.venv\Scripts\python.exe .\scripts\download_include_reference_clips.py `
  --profile validation-all `
  --ai4bharat-repo C:\AIAssets\INCLUDE `
  --destination C:\AIAssets\INCLUDE_videos `
  --download
```

Review the downloader report and ensure the expected 77 files are present. Preserve the original video files; do not resize/re-encode them.

## 5. Capture actual browser Canvas frames

Use the packaged helper (it checks Python 3.12, `.env`, the empty IoT map and the pinned repo before starting):

```powershell
.\scripts\run_phase1_browser_capture.ps1 `
  -RepoPath C:\AIAssets\INCLUDE `
  -VideoRoot C:\AIAssets\INCLUDE_videos `
  -Manifest .\outputs\include50_official_val_manifest.csv `
  -OutputDir .\outputs\browser_canvas_validation
```

The helper opens `http://127.0.0.1:8765/` and starts the local capture receiver. In the page:

1. Choose the folder containing the extracted official validation videos.
2. Verify all 77 rows match. Missing or ambiguous clips block capture.
3. Select **Capture all 77 clips**. The page processes one clip at a time and resumes clips already validated by the receiver.
4. Keep the browser tab active; do not pause or seek the videos.
5. Wait for the page to report 77/77 received, then stop the receiver with Ctrl+C.

The receiver writes:

- `capture_index.json` — official item, file SHA-256, browser metadata and completeness status;
- `clips/*.islbrw` — validated binary payload containing each exact Canvas JPEG frame;
- `frames/<capture_id>/*.jpg` — decoded frame copies for inspection.

To replace already saved clips, restart the helper with `-Overwrite` and explicitly check **Recapture existing clips** in the page. Otherwise, existing verified captures are skipped and orphaned files fail closed rather than being overwritten.

A partial capture set is resumable. Do not mark the benchmark complete unless `capture_index.json` reports all 77 and the evaluator accepts exactly 77 containers.

## 6. Run paired feature and model evaluation

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate_browser_canvas_validation.py `
  --manifest .\outputs\include50_official_val_manifest.csv `
  --capture-dir .\outputs\browser_canvas_validation `
  --data-root C:\AIAssets\INCLUDE_videos `
  --repo C:\AIAssets\INCLUDE `
  --checkpoint C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth `
  --text-threshold 0.40 `
  --out-dir .\outputs\browser_canvas_evaluation
```

The evaluator fails closed unless it verifies:

- exact pinned AI4Bharat HEAD and official validation-list hash/count;
- the exact documented one-line dropout worktree patch;
- locked checkpoint SHA-256;
- full 50-class label map and official 77-item `val` split (test split remains locked);
- all 77 original videos and all 77 browser capture containers;
- `ISL_IOT_ENABLED=false`, hard pre-IoT lockdown and zero mappings;
- configured text threshold equals the requested benchmark threshold.

The script does not update or fine-tune the model, change the class map, change the 134-D layout or change 169-frame preprocessing.

## 7. Outputs and interpretation

Expected outputs:

- `metrics.json` — complete environment, verified asset hashes, methods, coverage, top-1/top-5, macro/weighted F1, per-class report, confusion matrices and risk/coverage thresholds;
- `per_clip.csv` — browser and OpenCV path frame counts, pose/hand coverage, failures, gates, predictions, confidence and paired differences;
- `browser_confusion_matrix.csv` and `raw_confusion_matrix.csv`.

Report the two paths separately. Do not call their difference a pure Canvas resize penalty: they also differ in browser vs OpenCV video decoding. The frame-paired feature summaries exclude any pair where either extraction failed; the model input uses successful extracted frames in order, then the existing 169-frame preprocessing.

This local-video benchmark isolates Canvas encoding and feature extraction. It does not include live webcam sensor/camera behavior, WebSocket network backpressure, browser permission UX or user-signing variability. Those require later phases and separate evidence.

## 8. Do not do during Phase 1

- Do not change model, checkpoint, class order, preprocessing, thresholds or production JPEG settings before measuring the baseline.
- Do not re-run old Q72 sweeps or the completed 20-seed dropout experiment.
- Do not substitute a different validation/test split or use metadata-only records as video samples.
- Do not enable IoT, populate mappings, run MQTT, launch the simulator or connect hardware.
- Do not report historical handover metrics as a new result.

## 9. Current blocker status

As of this package build, Phase 1 has not been executed: the uploaded archives do not include the upstream Git clone, checkpoint or 77 video files, and the review container lacks MediaPipe. Run the above commands on the existing target Windows Python 3.12 environment once the assets are available. The package's software test result is not a substitute for this benchmark.

---

## Superseding execution result — 7 November 2026

The target Windows environment subsequently completed this runbook. The official validation split had 77 entries / 50 classes; all 77 local source clips resolved. Browser capture completed 77/77 (the capture resumed from clip 15 after a transient low-frame failure; no force-recapture was used). The paired evaluator completed all 77 clips. Browser Canvas top-1 44/77 (57.14%), top-5 67/77 (87.01%), macro F1 0.5207; OpenCV native reference top-1 46/77 (59.74%), top-5 69/77 (89.61%), macro F1 0.5594. The 0.40 threshold remains uncalibrated. Full evidence is retained in `engineering/phase1_evidence/`. The earlier “not executed” statement above described the pre-execution package snapshot and is superseded by this result.
