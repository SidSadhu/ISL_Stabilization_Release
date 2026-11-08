# ISL Capture Stabilization — Update 2026-11-08

## What changed

This is a conservative capture/debugging update based on the user's latest four guided-capture attempts and the supplied project source snapshot.

1. **Camera negotiation and telemetry** — request an ideal 1280×720, 16:9, 25 FPS webcam mode; fall back to a 640×480 ideal request if the browser rejects the preferred request. The app shows the actual negotiated camera track settings. It never crops or stretches the classifier input to force 16:9.
2. **Stable framing gate** — before the existing 3-second countdown, wait for 5 rolling local MediaPipe preview samples. Readiness means the upper-body pose has at least 8 visible points and at least one hand is detected in at least 4 of the last 5 samples. This is a framing check, not sign recognition, and deliberately does not require both hands for one-handed signs. The user can turn this gate off to use the previous countdown flow.
3. **Rolling live feedback** — the panel reports recent five-sample coverage instead of treating one missed preview frame as a capture-wide failure.
4. **Avoid competing MediaPipe sessions** — the separate landmark-preview websocket is closed during capture and post-capture extraction, then resumed when the app returns to its ready state.
5. **Frame timing report** — the backend returns observed client timestamp FPS and mean/median/p95 frame interval. These are capture-send timing indicators, distinct from MediaPipe extraction time.
6. **Opt-in local debugging** — an unchecked-by-default checkbox saves the exact received JPEG frames, frame-by-frame pose/hand detection records, the raw 134-D feature rows, the interpolated sequence, the padded 169×134 model input, actual camera settings, capture intervals, and final prediction/top-five result. The local debug save is capped at 32 MiB per capture. Files are stored under `outputs/debug_captures/<capture-id>/` and may contain the user's face; delete them after debugging.
7. **Clearer result wording** — when a score passes the provisional threshold but the predicted label differs from the selected expected sign, the UI explicitly says that the expected sign did not match. “Accepted” is not presented as proof of correct recognition.
8. **Validation evaluator included** — `scripts/evaluate_include50_validation_keypoints.py` evaluates the frozen checkpoint on a supplied official validation keypoint directory. It produces top-1/top-5 accuracy, per-class metrics, a 50×50 confusion matrix, top confusions, and a threshold/coverage sweep. It does not choose a threshold or modify model weights.

## Intentionally unchanged

- Official INCLUDE-50 model checkpoint and model architecture.
- Label map and class index order.
- 134-dimensional feature layout (25 pose points + 21 hand 1 + 21 hand 2).
- Existing coordinate scaling, interpolation, truncation and 169×134 padding semantics.
- Provisional 0.40 acceptance threshold. It remains uncalibrated until a complete, independently held-out validation evaluation is reviewed.
- IoT lockdown. No device commands are enabled by this patch.
- Existing record-then-extract behavior. Frames are buffered first and extracted after capture; slow post-capture landmark processing does not mean frames were recorded at that slow rate.

## Apply

Run the PowerShell installer in the release ZIP:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& "$HOME\Downloads\ISL_CAPTURE_STABILIZATION_RELEASE\APPLY_ISL_CAPTURE_STABILIZATION.ps1" `
  -ProjectRoot "C:\Users\siddh\Downloads\ISL_Stabilization_Release\ISL_Stabilization_Release"
```

The installer verifies that the five source files match the exact versions in the supplied source bundle, creates a timestamped backup under `backups`, applies the patch, and verifies the resulting SHA-256 hashes. It aborts rather than overwriting unexpected local edits.

Restart the app after applying the patch:

```powershell
& "C:\Users\siddh\Downloads\ISL_Stabilization_Release\ISL_Stabilization_Release\scripts\start_app.ps1"
```

## Recommended workflow

1. Open the app and enable the camera preview.
2. Check the actual camera resolution/FPS displayed by the app. A 16:9 result is preferred, but a different returned mode is not blocked.
3. Leave **Wait until framing is ready** enabled. Move to the signing position and wait for the app to see a stable upper body and at least one hand in 4/5 preview samples.
4. Let the 3-second countdown begin. Guided timed capture remains 2.6 seconds and stops automatically; manual capture is still available by disabling the timed mode.
5. For a failed or surprising attempt, enable **Save this capture for debugging** before recording. The folder will contain the evidence needed to inspect timing, camera settings, frame-level detection and the exact model tensor.
6. Keep the app tab visible during capture. Background capture remains unsupported by design.

## Run targeted tests

```powershell
$Project = "C:\Users\siddh\Downloads\ISL_Stabilization_Release\ISL_Stabilization_Release"
& "$Project\.venv\Scripts\python.exe" -m pytest -q `
  "$Project\tests\test_guided_readiness_debug_capture.py" `
  "$Project\tests\test_live_landmark_diagnostics.py" `
  "$Project\tests\test_preprocess.py" `
  "$Project\tests\test_preprocessing_contract.py"
```

## Run the official validation evaluation (separate from capture changes)

The evaluator requires a complete pre-generated AI4Bharat INCLUDE-50 **validation** keypoint directory, usually named `include50_val_keypoints`. The 132 selectively downloaded demo clips are not a substitute for the full official validation split.

Example (adjust the checkpoint path to the actual file on your machine):

```powershell
$Project = "C:\Users\siddh\Downloads\ISL_Stabilization_Release\ISL_Stabilization_Release"
& "$Project\.venv\Scripts\python.exe" "$Project\scripts\evaluate_include50_validation_keypoints.py" `
  --keypoints-dir "C:\AIAssets\INCLUDE\include50_val_keypoints" `
  --ai4bharat-repo "C:\AIAssets\INCLUDE" `
  --checkpoint "C:\AIAssets\INCLUDE\models\include50_no_cnn_transformer_small.pth" `
  --output-dir "$Project\outputs\include50_validation_evaluation"
```

Outputs include `validation_report.json`, `confusion_matrix_50x50.csv`, `per_class_metrics.csv`, `top_confusions.json`, `predictions.csv`, and `threshold_sweep.csv`. A threshold must not be called calibrated from a limited smoke test or an incomplete demo subset. Keep the app's 0.40 setting unchanged until the complete evaluation is reviewed.

## Hello reference material

- `docs/reference_guides/INCLUDE_HELLO_FRAME_BY_FRAME_GUIDE.png` — sampled original frames from the user's official `MVI_0032.MOV` reference clip.
- `docs/reference_guides/MEDIAPIPE_21_HAND_LANDMARK_MAP.png` — standard MediaPipe hand landmark index topology.
- `scripts/export_include_hello_landmarks.py` — optional script to run the local MediaPipe detector over the official reference clip and export actual annotated frames, numbered hand points and JSON coordinates. The compatibility pose visualization uses modern full-pose indices 0–24 and is **not** claimed to reproduce the historical `upper_body_only=True` detector exactly.

## Rollback

The installer creates a timestamped backup. To roll back, stop the server and copy the five backed-up source files from the latest folder under `backups\capture_stabilization_*` back to their original paths. Do not delete the backup until the updated app has been exercised successfully.
