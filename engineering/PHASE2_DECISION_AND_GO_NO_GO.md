# Phase 2 Decision Register — 7 November 2026

## Phase 1 evidence status

**GO:** Phase 1 browser-native capture and paired validation evaluation completed on all 77 official validation clips. Source artifacts were supplied by the Windows run and analysed into `engineering/phase2_evidence/`.

## Findings accepted

1. Browser Canvas top-1 = 44/77 (57.14%); OpenCV native top-1 = 46/77 (59.74%).
2. Top-1 agreement = 66/77; 43 clips are correct in both paths and 30 are wrong in both.
3. Browser extraction produced 4,753/4,753 feature frames, with zero extraction failures; the OpenCV reference had 26 failed extractions.
4. Browser-vs-native feature MAD is larger for the 42-D hand slots than for the 50-D pose slot. This warrants targeted ablation but does not establish a causal defect.
5. The official validation set has 23 singleton classes and 27 two-example classes; metrics are noisy class-level estimates and threshold analysis remains exploratory.
6. Historical MediaPipe version/build is unpinned in upstream requirements. The modern first-25 adapter is not historical model parity.

## Phase 2 implementation status

- Source/provenance audit: complete.
- Phase 1 evidence analysis and charts: complete.
- Four-path 77-video runner: implemented.
- Phase 2 report/figure generator: implemented; it fails closed unless all 77 diagnostics succeeded.
- Runner integrity/resume guards: implemented.
- Diagnostic transform regression tests: added.
- Phase 2 full 77-video execution on target Windows: **PENDING** (requires the local video corpus, pinned repo and checkpoint).

## Accepted corrective changes in the engineering tooling

- Removed an accidental generation of the obsolete q72 diagnostic branch; q92 is the only JPEG condition.
- Renamed the q92-vs-upstream comparison to `opencv_q92_vs_upstream` so an OpenCV approximation cannot be mistaken for actual browser Canvas.
- Added runtime/version and source-file hashes to the resumable-run fingerprint.
- Require the exact documented upstream dropout-only diff before Phase 2.
- Require Python 3.12, MediaPipe 0.10.21, OpenCV 4.11.0 and PyTorch 2.11.0 baseline.
- Added an explicit output-directory reuse guard and subprocess timeout.

## Not approved

- No checkpoint, Transformer architecture or class ordering change.
- No feature dimension or sequence length change.
- No change to x×1920 / y×1080 preprocessing.
- No hand-slot assignment change.
- No confidence threshold calibration/change.
- No MediaPipe downgrade without authoritative version/wheel/source provenance.
- No IoT activation, MQTT, simulator or ESP32 activity.
- No use of the official test split.

## Next gate

**Phase 2 controlled ablation must complete 77/77 successfully** and be reviewed before a narrow implementation correction is proposed. If the controlled result isolates a resize/JPEG effect, implement only the supported transform fix; if the modern app-vs-upstream delta persists, inspect mapping/configuration in per-clip evidence. If the remaining limitation is historical MediaPipe provenance, report it explicitly rather than changing detector versions speculatively.

**Release status remains NO-GO for a validated recognition demonstration release** until Phase 2, user webcam validation, and the subsequent release gates in the handover plan are complete.
