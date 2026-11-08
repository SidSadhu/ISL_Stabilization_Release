# Phase 2 failure diagnosis and targeted recovery

**Date:** 7 November 2026  
**Affected validation item:** `Adjectives/79. short/MVI_9302.MOV`  
**Initial Phase 2 result:** 76 successful / 1 failed out of 77.

## Observed failure

The uploaded target-Windows diagnostic bundle records this exception for the clip above:

```text
AttributeError: 'NoneType' object has no attribute 'landmark'
```

The other 76 per-clip records are successful. The Windows regression log accompanying the run reports `169 passed, 1 warning`; this is the test result for the installed Phase 2 candidate before the recovery patch. The Phase 2 experiment itself is incomplete and its report builder correctly rejected it.

## Root cause

The pinned AI4Bharat `generate_keypoints.py` implements `process_pose_keypoints(results)` by reading `results.pose_landmarks` and passing it directly to `process_landmarks()`. `process_landmarks()` dereferences `landmarks.landmark` without handling `None`. MediaPipe may return a valid frame result with `pose_landmarks=None` when Pose detection fails on that frame. That upstream helper then raises the recorded `AttributeError`.

The repeated MediaPipe `inference_feedback_manager` messages are warnings; the fatal error is the `NoneType.landmark` dereference. This is an upstream diagnostic-generator edge case, not a checkpoint-load error or Transformer inference error.

## Approved narrow correction

The Phase 2 diagnostic now installs a process-local adapter around the imported upstream `process_pose_keypoints` function. When Pose landmarks are absent, it returns 25 NaN x-coordinates and 25 NaN y-coordinates. The pinned upstream source checkout is not edited. This sentinel behavior follows the upstream dataset's own missing-observation representation: missing coordinates are stored as NaN and interpolated later by the dataset pipeline. If a hand is present while Pose is absent, NaN wrists prevent the upstream one-hand wrist comparison from making an arbitrary left/right reassignment; detector order is retained, matching the app's policy of not assigning a hand without valid pose wrists.

The shim is limited to the diagnostic compatibility process. It does not alter the production extractor, checkpoint, model, class vocabulary, feature slots, preprocessing scale or sequence length. The diagnostic records the number of missing-Pose frames encountered by the upstream path.

## Targeted resume of the known 76/77 run

The runner includes an explicit `--resume-compatible-missing-pose-guard` option. It may reuse prior successful clip reports only if:

1. the cached report has `status=success` and the same official path, label and source-video SHA-256;
2. its prior diagnostic and runner hashes exactly equal the two known hashes from the uploaded 76/77 run;
3. all other runner fingerprint fields (repository commit, split and manifest hashes, labels, checkpoint, runtime, application code and model contract) match the current fingerprint exactly.

A successful prior clip could not have triggered the old missing-Pose dereference; therefore the added fallback is not invoked for that clip and its computed values are unchanged. The one failed clip has no successful cached result and is rerun with the guard. If any cache item is missing or fails these compatibility checks, the runner recomputes that clip rather than reusing it. Do not use `--force`.

## Evidence required for completion

Phase 2 remains incomplete until the new target-Windows `summary.json` reports `status=completed`, `selected_count=77`, `successful_count=77`, and `failed_count=0`, and the fail-closed report builder succeeds. Keep the official validation split, checkpoint, 50-class order, 134-D feature contract, 169-frame input, 0.40 diagnostic threshold and IoT-off state unchanged. The official test split remains locked.
