# Known Limitations — ISL Manual INCLUDE-50 Pre-IoT Release v1.0.0-rc3

## P0 — Must resolve before a production or physical-IoT claim

1. **Pose detector parity is not established**  
   The upstream INCLUDE generator uses the historical MediaPipe Pose option `upper_body_only=True`. That argument was removed in MediaPipe 0.8.4 while the Pose model was updated. The app uses the modern full-body Pose model and slices indices 0–24. This preserves the 25 feature slots but does not prove coordinate-level parity. Compare both feature pipelines on identical frames before changing or certifying the extractor.

2. **Webcam recognition quality remains unresolved**  
   Previous webcam attempts showed variable hand coverage (roughly 35% to 60% in the discussed captures) and incorrect/low-confidence predictions, including a rejected prediction despite a higher-coverage capture. Framing, lighting, camera orientation, detector differences, JPEG encoding, temporal trimming and signer variation are all possible contributors. Do not attribute the errors to framing alone and do not lower quality/confidence gates to force acceptance. Use the manual Start/Stop flow, record diagnostics and compare controlled captures against the full official validation baseline.

3. **Confidence thresholds are uncalibrated**  
   The text-recognition threshold `.40` was previously chosen from an exploratory 29-reference-clip sweep, not an independent held-out deployment set. The IoT threshold `.70` is a separate provisional actuator gate, not a safety certification. Run the exact 77-example official validation evaluation and collect a separately labelled webcam set before selecting thresholds. Raw softmax scores are not calibrated probabilities.

4. **No independent signer/webcam validation**  
   The official dataset is not a substitute for independent signers, camera setups or regional sign variants. Evaluate signers not represented in the reference data, and include non-INCLUDE gestures, idle movement, partial occlusion, both one-hand and two-hand signs, and varied lighting.

5. **Checkpoint/detector smoke test pending in this package**  
   The strict model-contract test is skipped in the isolated work copy because the external AI4Bharat checkout and checkpoint are not present. The user's external checkout has a deterministic-dropout patch from prior work; verify its exact source state and checkpoint hash on Windows before inference.

## P1 — Performance and product hardening

6. **MediaPipe extraction latency**  
   Historical diagnostics showed feature extraction taking seconds per clip while Transformer inference was in the millisecond range. Re-measure on the target Windows laptop, including p50/p95 Stop-to-result latency and the extra landmark-preview workload. Optimise the measured bottleneck rather than changing the model first.

7. **Official validation has not been run end to end**  
   The pinned official validation path list has 77 entries spanning all 50 labels. The current local video folder previously contained only a subset; run the `validation-all` downloader and evaluator. Do not use test videos for model or threshold selection. The 29-clip gallery result is a small diagnostic benchmark, not a full split result.

8. **Open-set rejection is not calibrated**  
   The softmax classifier always ranks one of its 50 known classes. It can produce a high score for an out-of-vocabulary sign or arbitrary movement. Define and test a rejection policy using negative examples and multiple signers.

9. **IoT is deliberately locked**  
   The shipped map contains zero signs, the app-level pre-IoT lock is hard-coded on, and firmware defaults to an empty allowlist plus `LED_GPIO=-1`. The owner must choose two labels. After that, a separate release must align backend and firmware mappings, validate broker authentication/ACLs, compile for the exact board, and pass supervised electrical tests.

10. **MQTT semantics are at-most-once, not exactly-once**  
    The backend's private capture-ID ledger survives visible history reset and retention; firmware stores the most recent 64 capture/command IDs. This prevents normal retries from repeatedly actuating, but a failure after backend reservation can lose an action rather than retry it. Do not assume exactly-once delivery.

## Software boundaries verified in the isolated work copy

- Official INCLUDE-50 label order and `(169, 134)` model-input shape are covered by tests.
- The full 77-item validation manifest parser verifies the pinned path-list hash, unique safe paths and label coverage; the user's local Git HEAD check and actual evaluation remain pending.
- The pre-IoT lock, empty mapping default, backend capture ledger, retention/reset behavior, simulator validation and firmware source guards are covered at software test seams.
- None of these tests establishes real model accuracy, live camera behaviour, MQTT interoperability, firmware compilation or physical actuation.

---

## Superseding execution update — 7 November 2026

Limitations 5 and 7 above are historical package-build statements and are superseded by the target-Windows evidence recorded later:

- The official checkpoint strict-load test passed on Windows (1 passed in 7.35 seconds).
- The official 77-clip validation split was resolved and captured 77/77; paired inference completed 77/77.
- Browser Canvas top-1 was 44/77 (57.14%) and OpenCV native top-1 was 46/77 (59.74%). Full evidence is retained in `engineering/phase1_evidence/` and analysed in `engineering/PHASE2_ROOT_CAUSE_ANALYSIS.md`.

The remaining limitations still apply: historical MediaPipe `upper_body_only=True` detector parity remains unproven; 0.40 is uncalibrated; the 77-example validation split has low per-class support; external signer/webcam validation remains outstanding; IoT/hardware remains locked. Phase 2's all-77 preprocessing/feature provenance ablation is implemented but still awaits target-Windows execution.
