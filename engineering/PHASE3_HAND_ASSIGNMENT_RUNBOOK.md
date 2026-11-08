# Phase 3 Runbook — Hand Assignment & Detector Stability

## Objective

Phase 2 showed that feature differences were concentrated in hand slots, but its OpenCV resize/JPEG paths were only browser-transport approximations. Phase 3 directly compares the saved, exact Canvas JPEG payloads from Phase 1 against source-video frames sampled at the corresponding browser `video_time_seconds`.

The diagnostic separates five questions:

1. **Detector presence:** did MediaPipe return zero, one, or two hands?
2. **Handedness output:** what image-side `Left`/`Right` labels and confidence scores did MediaPipe return, and in what detector order?
3. **Slot assignment:** which detector output was placed in `hand1` and `hand2` by the current `verified_one_hand_fix` rule?
4. **Counterfactual:** would the offline `wrist_nearest` policy put the two detector hands into different feature slots? It is a diagnostic only and is never applied to production.
5. **Coordinate and interpolation effects:** for uniquely matching handedness labels, how different are the complete 21-point coordinates after the project's 1920/1080 scale? How many missing pose/hand feature coordinates are filled by temporal interpolation, and what is the longest consecutive missing run?

## Fixed selection

| Clip | Role |
|---|---|
| `Greetings/51. Good Morning/MVI_0099.MOV` | Phase 2 hand-slot/prediction outlier |
| `Colours/55. White/MVI_5063.MOV` | Phase 2 hand-presence/slot outlier |
| `Adjectives/83. big large/MVI_9396.MOV` | Phase 2 hand-coordinate drift outlier |
| `Adjectives/79. short/MVI_9302.MOV` | recovered missing-pose edge case |
| `Means_of_Transportation/11. Car/MVI_3118.MOV` | stable recognition control |
| `Pronouns/40. I/MVI_9922.MOV` | stable recognition control |

These are drawn from the pinned official INCLUDE-50 validation manifest. The runner verifies the 77-row manifest against the pinned repository split, then requires the exact saved browser capture for each of these six paths and resolves the corresponding local source video. It does not download assets.

## Exact input comparison

- `browser_canvas`: exact JPEG byte payloads read from each `.islbrw` capture container. The bytes are not re-encoded before MediaPipe sees them.
- `native_timestamp_matched`: source video frames decoded through OpenCV at `round(video_time_seconds * source_fps)`, encoded nowhere, and passed into the detector as decoded BGR frames.
- The maximum nearest-frame timestamp error is recorded per clip. Normal samples must remain within half a source-frame interval. If the browser timestamp rounds to or beyond the declared frame count but is no later than half a frame after the container duration, the runner explicitly reuses the final decoded source frame and flags that sample as `source_terminal_frame_hold=true`. This models HTML video holding its final image at end-of-stream. That bounded terminal hold may differ from the frame's presentation timestamp by up to 1.5 frame intervals. Any timestamp later than that boundary, or any decode failure before the final frame, remains a hard error.
- The two detector streams each use a fresh sequential MediaPipe Hands/Pose session. Differences therefore include resolution/encoding and expected tracking-state effects from each sampled stream; they are not a pixel-synchronous replay of identical decoder state.

## Files produced

- `phase3_summary.json`: run provenance, runtime, per-clip aggregated detector/assignment/interpolation evidence, and file hashes.
- `phase3_clip_summary.csv`: one row per clip with compact JSON-valued diagnostic fields.
- `phase3_per_frame_hand_diagnostics.csv`: frame-level count, detector-order labels/scores, pose-wrist distances, production/counterfactual detector-to-slot mappings, hand presence, and all 21 raw normalized landmark coordinates for each detected hand.
- `phase3_per_hand_coordinate_comparison.csv`: paired, unique image-side handedness-label matches and full 21-point coordinate MAE/P95 after 1920/1080 scale, plus wrist displacement.
- `PHASE3_HAND_ASSIGNMENT_REPORT.md`: readable report with per-clip comparisons and interpretation guardrails.

## Guardrails

- The classifier and checkpoint are not loaded. This is not a recognition accuracy test.
- No model training, fine-tuning, test-split access, class-order change, feature-dimension change, threshold tuning, or preprocessing change occurs.
- No production extractor code is altered. The script uses the existing mapping implementation and runs the `wrist_nearest` policy only as an in-memory counterfactual.
- IoT must remain hard-locked, `ISL_IOT_ENABLED=false`, and the IoT label mapping must remain empty. No MQTT client or hardware command path is imported or called.
- MediaPipe handedness labels are image-side classifications. With the current unmirrored classifier input, do not state that `Left` or `Right` proves anatomical left/right identity. Matching the same label across paired images is a diagnostic matching proxy only.
- A wrist match is insufficient evidence by itself. The coordinate CSV also compares all 21 landmarks when a unique matching label exists; unmatched/ambiguous detections are excluded rather than guessed.
- `wrist_nearest` is not promoted to production just because it appears more anatomically intuitive. Any production assignment change would require separate evidence that the checkpoint was trained with the same slot semantics and a controlled evaluation after the choice is justified.

## Run

From the project root in Windows PowerShell, run the single consolidated command in the Phase 3 handoff. It runs the full tests first, executes the diagnostic, saves persistent logs, and creates a result bundle ZIP. It does not overwrite the Phase 1 capture files.
