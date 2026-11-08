> **Historical decision note (superseded 6 November 2026):** the original single-label `hello → LED_BLINK` sketch is no longer the product scope. The current product keeps INCLUDE-50 text recognition and leaves the IoT map empty until the owner chooses exactly two signs. The current web text threshold is `.40` (uncalibrated); `.70` is reserved as a separate provisional IoT gate.

# Architecture Decision Record — ISL Pre-IoT Release v1.0.0

**Author:** Claude (Sonnet 4.6), acting as lead research/production ML engineer  
**Date:** 2026-10-29  
**Status:** Accepted for pre-IoT milestone

---

## 1. Problem Statement

Deliver a reproducible Pre-IoT release of a real-time isolated ISL gesture recognition system using the AI4Bharat INCLUDE-50 pretrained Transformer-small checkpoint, with honest evaluation, correct preprocessing, and a safe confidence policy.

Three P0 issues required architectural decisions:

| Issue | Decision |
|---|---|
| MediaPipe `upper_body_only` removed | Preserve the 25 input slots; exact legacy detector parity remains unverified |
| Confidence threshold 0.70 rejects most correct clips | Lower to 0.40 provisional, show all top-5 |
| Feature extraction 5–15 s per clip | Single-session reuse + profiling baseline |

---

## 2. Option Comparison: Model Architecture

### Option A — INCLUDE-50 pretrained Transformer-small ✅ SELECTED

**Rationale:** The pretrained checkpoint exists locally and produces correct 50-class outputs. The 29-clip benchmark achieved 79.31% top-1 (640px) / 75.86% (native), 96.55% top-5 both runs. These are not independent test results, but confirm the checkpoint is functional. No data access, training pipeline, or compute justifies fine-tuning (Option B) at this stage.

**Limitation retained:** the current MediaPipe adapter preserves the expected input slots, but historical detector coordinate parity remains unverified.

### Option B — Fine-tune / retrain

**Rejected.** No signer-disjoint validation set available locally. The 29 reference clips are from the training distribution (potential overlap unknown). Fine-tuning on these would not produce a valid evaluation. Re-use this option when properly split data is available.

### Option C — Alternative CV/DL model

**Rejected.** No compelling evidence of improvement over the 79.31% top-1 benchmark. Switching models without a proper comparative baseline would be scientifically unsound. The AI4Bharat requirement is also explicit in the project scope.

### Option D — Hybrid real-time pipeline

**Deferred to post-IoT.** The live preview WebSocket already uses a separate MediaPipe session for visual guidance. The classifier pipeline uses its own session (record-then-extract). They are architecturally separated. No change needed now.

---

## 3. Pose preprocessing: legacy `upper_body_only` compatibility

> **Correction recorded 6 November 2026:** the earlier draft below overstated equivalence. The first-25 slice preserves feature slots, but it is not evidence that the modern detector reproduces the historical detector coordinates.

### The problem

Upstream `generate_keypoints.py` calls MediaPipe Pose with `upper_body_only=True`. MediaPipe removed that argument in **v0.8.4** while updating the Pose model. The application uses the modern full-pose detector and selects the first 25 landmarks.

### Current engineering decision

Keep the first-25 adapter temporarily because the existing checkpoint expects 25 pose points per frame and the first 25 output slots retain the expected upper-body landmark index order. Keep the 134-D feature dimension and 169-frame sequence unchanged. This is **input-slot compatibility**, not a claim of identical detector behaviour.

The updated detector can produce different landmark coordinates, tracking behaviour or missing-point patterns. `pose_landmarks[:25]` therefore does not prove equivalent model input to the historical `upper_body_only=True` generator.

**Status: SLOT-COMPATIBLE; COORDINATE-LEVEL PARITY UNVERIFIED.** A paired comparison on identical frames, using a compatible historical MediaPipe environment or a verified port of its detector/model assets, is required before claiming parity or changing the current path.

**Wrist indices:** the app retains the upstream single-hand placement convention at pose indices 15 and 16. Keep this policy unless a paired feature comparison shows a systematic issue.

---

## 4. Confidence Threshold Policy

### Current state

The 0.70 threshold is uncalibrated and conservative. Benchmark results:
- At 0.70: 5/29 accepted, 5/5 correct (100% selective accuracy, but 83% rejection rate)
- At 0.40: 14/29 accepted, 14/14 correct (100% selective accuracy, 52% rejection rate)
- At 0.30: 24/29 accepted, 22/24 correct (91.7% selective accuracy)

**Warning:** All these numbers are from the SAME 29 clips used to sweep thresholds. This is not independent validation. Overfitting to these 29 clips is likely.

### Decision: 0.40 provisional, show all top-5, UNCALIBRATED badge ✅

**Rationale:**
- 0.70 makes the system unusable for demos (rejects ~83% of correct predictions)
- 0.40 gives reasonable coverage while maintaining selectivity on the tiny benchmark
- The UI **always** shows the raw score, all top-5 candidates, and a prominent UNCALIBRATED warning
- Rejected predictions still show the candidate label and reason ("Candidate: X · Score Y% · Below threshold")
- This is transparent and honest; users understand the system is not production-calibrated

**Required future work:** Tune threshold on a proper independent validation set with stated precision/recall targets. Use temperature scaling for calibrated probabilities.

---

## 5. Extraction Performance

### Measured baseline

5–15 seconds per ~100-frame clip on the user's CPU. Primary bottleneck: sequential cv2.imdecode + MediaPipe per frame.

### Optimization applied in this release

1. **Single session reuse** (already in baseline; confirmed preserved)
2. **model_complexity=1** (default; explicit setting to prevent accidental regression)
3. **No preview allocation** during batch extraction (feature path uses `extract()`, not `extract_with_diagnostics()`)
4. **np.frombuffer** instead of np.fromstring (zero-copy decode)

### Not applied (deferred)

- Parallel frame decode: MediaPipe solutions are not thread-safe in this mode
- Resolution downscaling: benchmark shows native slightly faster (5.65s vs 7.49s mean) but slightly lower top-1 (75.86% vs 79.31%); clear winner unclear
- Worker process pool: would add complexity; profile first

**Realistic target:** 3–8 seconds per 100-frame clip on the user's CPU after baseline profiling. The extraction bottleneck is MediaPipe inference per frame, not I/O.

---

## 6. Stochastic Dropout Fix

**Status: Verified fixed in prior session.**

The user applied a patch to `C:\AIAssets\INCLUDE\models\transformer.py` replacing `F.dropout(x, p=0.2)` with `F.dropout(x, p=0.2, training=self.training)`. Five repeated inferences on the same input returned identical scores after the fix.

**This release:** Does NOT re-apply the patch. Does NOT modify external checkout. The regression test `test_preprocessing_contract.py::TestDeterminism` verifies determinism at the preprocessing level. A model-level determinism test (requires checkpoint access) is in `test_model_contract.py::test_official_checkpoint_loads_strictly`.

**Required user action:** Verify the patch is still applied to `C:\AIAssets\INCLUDE\models\transformer.py` before running the app. Script: `scripts\fix_include_inference_dropout.ps1` (already in project).

---

## 7. IoT Architecture — current rc3 implementation

**Status: implemented behind a hard pre-IoT lock; no physical actuation enabled in this release.**

The current product keeps the full INCLUDE-50 classifier for text output. IoT is a separate backend route with an empty-by-default `config/iot_label_map.json`. A future reviewed IoT release requires exactly two official labels selected by the project owner; no labels are chosen by this package.

The backend requires an accepted recognition result, exact allowlist membership, a separate provisional IoT confidence gate, session-level enablement, cooldown and valid command/TTL fields. The current release also keeps `Settings.pre_iot_lockdown = True`, so API enable attempts and command publication remain suppressed.

The simulator requires exactly two mappings and validates the same command protocol. The ESP32 firmware requires two explicit compile-time label/command pairs, has an empty default allowlist, and keeps `LED_GPIO=-1`. It persists the most recent 64 command/capture IDs in Preferences/NVS before GPIO output. The backend separately persists capture IDs in a SQLite anti-replay ledger that survives visible history reset and retention. This is at-most-once protection, not exactly-once delivery.

The former five-label example mapping (`hello`, `goodmorning`, `thankyou`, `happy`, `good`) is historical only and is not the default configuration. It must not be treated as the user's final IoT sign selection.

---

## 8. Production Risks and Known Limitations

| Risk | Severity | Status |
|---|---|---|
| Full 77-video official validation has not been run | HIGH | Open; path-manifest tooling is prepared, but checkpoint and video evaluation must run locally |
| Text threshold 0.40 was selected from a small exploratory 29-clip sweep | HIGH | UNCALIBRATED; require complete validation and risk/coverage analysis |
| Pose detector parity pending frame-level comparison | HIGH | The adapter preserves the 25 feature slots; coordinate-level equivalence is unverified |
| Webcam errors observed with variable hand coverage | HIGH | Root cause unresolved; compare frame quality, feature extraction, pose parity, temporal crop and signer variation; do not blame framing alone |
| Feature extraction takes seconds per clip in historical CPU runs | MEDIUM | Profile on target laptop; inference itself was much faster in prior measurements |
| No independent signer/webcam validation | HIGH | Open; collect a separately labelled signer-disjoint set |
| Current MQTT broker sample allows anonymous access on loopback only | HIGH for LAN use | Safe only for local simulation; physical LAN deployment requires authenticated broker, ACLs and firewall review |
| Firmware not compiled or physically tested | HIGH | Pre-IoT lock and `LED_GPIO=-1` remain in force |
| Capture idempotency is at-most-once | MEDIUM | Backend ledger survives history reset/retention; ESP32 retains 64 most recent capture IDs; failure after reservation may lose an action rather than retry |
