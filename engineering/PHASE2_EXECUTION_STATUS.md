# Phase 2 execution status — 7 November 2026

## Status

**Phase 2 analysis/tooling: complete. Target-Windows all-77 execution: pending.**

## Proven Phase 1 baseline

- Official validation: 77 clips / all 50 classes; pinned split SHA-256 `044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541`.
- Browser-native capture: 77/77 received; capture was resumed at clip 15 and prior clips were preserved.
- Target-Windows checkpoint strict-load: passed.
- Target-Windows pre-Phase-2 regression suite: 161 passed, 0 failed.
- Browser Canvas: top-1 44/77 (57.14%), top-5 67/77, macro F1 0.5207.
- OpenCV native: top-1 46/77 (59.74%), top-5 69/77, macro F1 0.5594.
- Same top-1 prediction: 66/77. Browser extraction failures: 0; OpenCV extraction failures: 26.

## Phase 2 controlled paths

1. `app_direct_native`: current application feature mapping on native OpenCV decoded frames.
2. `resized_640_png`: same mapping after OpenCV INTER_LINEAR resize to 640 px and lossless PNG transport.
3. `resized_640_jpeg_q92`: same resize and JPEG quality 0.92 encode/decode.
4. `upstream_original`: pinned upstream generator with modern MediaPipe full-Pose/first-25 compatibility adapter. This does **not** establish historical `upper_body_only=True` parity.

The 640 px OpenCV resize is an approximation of Chrome Canvas, not pixel identity. Phase 1 is the actual browser-captured reference. JPEG q72 is excluded.

## Run/acceptance conditions

Use `engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md`. The run writes one per-clip JSON and log, `summary.json`, and `per_clip_summary.csv`; `--resume` reuses successful fingerprint-matching clips. Acceptance requires `status=completed`, `successful_count=77`, and `failed_count=0`, plus a generated report in `report/`.

Do not change the checkpoint, model, class order, 134-D contract, 169-frame input, x×1920/y×1080 scaling, confidence threshold, or test split. Keep IoT disabled with an empty mapping and `pre_iot_lockdown=True`.

## Decision

No recognition remediation has been accepted yet. The high hand-feature MAD in Phase 1 is a hypothesis-prioritisation signal, not proof of a hand-slot defect. The Phase 2 ablation must identify the contribution of resolution/resize/JPEG/current-vs-upstream-modern mapping before a narrow correction is proposed. Historical MediaPipe build provenance and live independent-signer/webcam validation remain open. **Recognition-demo release remains NO-GO; IoT remains NO-GO.**

## Target-Windows execution update — 7 November 2026

The first four-path target-Windows run completed 76/77 clips. It is **incomplete**, not a passed Phase 2 campaign. The only failed item was `Adjectives/79. short/MVI_9302.MOV`, with `AttributeError: 'NoneType' object has no attribute 'landmark'` in the pinned upstream keypoint generator. Inspection showed that its `process_pose_keypoints()` helper dereferences `results.pose_landmarks.landmark` without handling a missing Pose result. The recurring MediaPipe feedback-manager messages in stderr are warnings, not the fatal exception.

A diagnostic-only missing-Pose adapter now maps an absent Pose result to 25 NaN x/y slots, consistent with the official upstream dataset's NaN/interpolation contract. It does not modify the pinned upstream checkout or production app. A narrow resume migration permits reuse of the exact known 76 prior successful per-clip reports only when their old diagnostic/runner hashes are the known values and every other source/runtime/asset/video fingerprint is identical. The failed clip is retried.

Recovery must be run with both `--resume` and `--resume-compatible-missing-pose-guard`; do not use `--force`. Acceptance remains 77/77 successful, zero failures, completed summary and successful report generation. The Windows test suite must also be rerun after this recovery patch.
