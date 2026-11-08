# Engineering Decision & Go/No-Go

**Decision date:** 7 November 2026  
**Release under review:** INCLUDE-50 manual recognition `1.0.0-rc3` stabilization candidate

## Decision summary

| Gate | Decision | Evidence / reason |
|---|---|---|
| Phase 0 source audit | **GO — complete** | Handover and source archives reconciled; actual implementation contracts and blockers documented. |
| Safe dependency/setup/package repairs | **GO — verified** | Added missing `xgboost==3.4.1` after strict checkpoint test exposed upstream unconditional import; Windows base suite passed. |
| Strict checkpoint loading | **GO — verified on target Windows** | `test_official_checkpoint_loads_strictly`: 1 passed in 7.35s; checkpoint hash matches. |
| Official validation split | **GO — verified** | Exact pinned 77-item list, 50/50 represented classes, split SHA-256 verified; all local clips resolved. |
| Browser Canvas capture | **GO — complete** | Receiver log confirms 77/77 clips captured and indexed. |
| Phase 1 paired model evaluation | **GO — complete as validation baseline** | 77/77 evaluated; browser Top-1 44/77 (57.14%), OpenCV native Top-1 46/77 (59.74%). This is not production readiness. |
| Phase 2 source/provenance audit | **GO — complete** | Pinned upstream generator, dataset preprocessing, class/feature layout and historical version gap documented. |
| Phase 2 four-path all-77 ablation tooling | **GO — implemented and review-tested** | Resumable runner, per-clip logs/JSON, output validation, source/runtime fingerprints and q92-only transform branch added. |
| Phase 2 four-path all-77 target-Windows execution | **PENDING** | Requires execution on the user's machine, where the 77 original videos, pinned repo and checkpoint reside. |
| Historical MediaPipe parity | **NO-GO — unproven** | Upstream `requirements.txt` does not pin the training-time MediaPipe version; modern first-25 adapter is not historical detector parity. |
| User webcam / cross-signer validation | **PENDING** | Reference video validation does not establish live-camera or independent-signer generalisation. |
| Recognition demonstration release | **NO-GO** | Phase 2 ablation, live-webcam validation and final release gates remain outstanding. |
| IoT / simulator / physical hardware | **NO-GO — remain locked** | `pre_iot_lockdown=True`, `ISL_IOT_ENABLED=false`, empty mapping; no IoT diagnostic commands are emitted. |

## Phase 1 results accepted as baseline

| Metric | Browser Canvas | OpenCV native reference |
|---|---:|---:|
| Top-1 | 44/77 (57.14%) | 46/77 (59.74%) |
| Top-5 | 67/77 (87.01%) | 69/77 (89.61%) |
| Macro F1 | 0.5207 | 0.5594 |
| Accepted at threshold 0.40 | 45/77 | 45/77 |
| Selective accuracy at 0.40 | 77.78% | 80.00% |
| Top-1 prediction agreement | 66/77 (85.71%) | 66/77 (85.71%) |

The 77-example validation set has low class support: 23 classes have one item and 27 classes have two items. The 0.40 recognition threshold is uncalibrated. Exploratory risk/coverage values must not be used as production threshold calibration.

## Non-negotiable configuration preserved

- INCLUDE-50 vocabulary and official class ordering unchanged.
- Transformer-small architecture and locked checkpoint unchanged.
- 134-dimensional feature layout unchanged.
- 169-frame model input unchanged.
- Coordinate scaling remains x×1920 / y×1080.
- Text threshold remains 0.40, provisional and uncalibrated.
- Current browser JPEG quality remains 640 px / 0.92.
- No repeat of the completed dropout seed experiment or obsolete q72 sweep.
- IoT disabled; empty map; no MQTT, simulator or ESP32 use.

## Phase 2 plan and release disposition

Run the all-77 Phase 2 four-path ablation using `engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md`. It compares current-app native extraction, 640px PNG, 640px JPEG q92, and the pinned upstream generator under the modern MediaPipe compatibility adapter. The OpenCV-resized q92 branch is explicitly an approximation of Canvas, not actual browser capture.

After the 77-path run, use the per-clip differences to choose one narrow corrective action, if supported. Do not infer a hand-slot bug solely from the Phase 1 hand MAD. Historical MediaPipe parity remains unproven unless authoritative training-time provenance is recovered.

**Current disposition:** Phase 0 and Phase 1 are complete. Phase 2 analysis/tooling is ready; its all-77 Windows execution is the next gate. The application remains an engineering stabilization candidate, not a validated recognition-demo release. IoT remains NO-GO.
