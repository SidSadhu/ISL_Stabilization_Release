# ISL Recognition — Stabilization Engineering Report

**Build date:** 7 November 2026  
**Baseline release:** `1.0.0-rc3`  
**Work performed:** Phase 0 complete; Phase 1 diagnostic tooling implemented, full model benchmark blocked by absent external assets.

## 1. Outcome

The complete handover package was read before examining the source archive. The handover is the authority for project intent and historical decisions; the current RC3 source was used as the authority for implementation status. The two were reconciled in `PHASE0_SOURCE_AUDIT.md`.

A clean, isolated working copy was created from the supplied source ZIP. Safe release defects were corrected and a browser-native Phase 1 validation workflow was added. No change was made to the locked recognition model, checkpoint, class ordering, feature slots, preprocessing or IoT policy.

## 2. Current implementation verified

- FastAPI application with plain JavaScript frontend.
- Manual Start/Stop isolated-sign capture; one inference after sequential post-capture extraction.
- 50 official INCLUDE-50 class labels and index order.
- 134 float32 features per frame and `(1,169,134)` model input.
- Canvas width 640 px, JPEG quality 0.92, 40 ms timer target and duplicate `video.currentTime` suppression.
- Text confidence gate 0.40 (uncalibrated); separate provisional IoT confidence gate 0.70.
- Code-level pre-IoT lock is true; zero default IoT mappings.

## 3. Source/package defects fixed

1. Added missing `.env.example` referenced by the Windows setup and launch scripts.
2. Replaced the wrong `httpx2` development dependency with Starlette TestClient's required `httpx` package.
3. Aligned Python package and FastAPI metadata to `1.0.0-rc3`; removed stale generated egg-info from the clean package.
4. Marked Q72 diagnostic material as historical and changed the old single-video diagnostic default to current JPEG quality 0.92.
5. Marked the eight-clip browser transport batch as a pilot, not the official 77-video benchmark.
6. Updated stale current-status/test counts and release notes.
7. Added a release checksum verifier and regenerated `SHA256SUMS.txt` for every packaged file (excluding the checksum file and dynamic runtime data).
8. Added explicit receiver resume/overwrite behavior and orphaned-file fail-closed handling.

## 4. Phase 1 implementation delivered

- `browser_capture_protocol.py`: binary container and strict payload validation.
- `serve_browser_validation_capture.py`: loopback-only HTTP receiver; exact pinned 77-row manifest guard; bounded uploads; local storage; verified resume; explicit overwrite.
- `browser_validation_capture.html`: actual browser video-to-Canvas JPEG capture with the deployed 640 px / 0.92 transform, 40 ms timer and 169-frame maximum.
- `evaluate_browser_canvas_validation.py`: separate browser Canvas and OpenCV-native timestamp-matched paths; coordinate differences by pose/hand slots; detector coverage/failure counts; padded model-input/logit/probability comparison; top-1/top-5, per-class, confusion and risk/coverage outputs.
- Evaluator refuses to run without the pinned upstream Git commit, exact single-line dropout patch, exact checkpoint SHA-256, exact official 77-item validation split, all 77 video/capture files, configured 0.40 threshold and an IoT-off/empty-map configuration.
- `run_phase1_browser_capture.ps1`, `RUN_COMPLETE_ISL_STABILIZATION.ps1`, and `verify_release_checksums.py` provide reproducible Windows entry points.

The browser capture isolates the Canvas transform and does not include production WebSocket backpressure or live webcam sensor behavior. The OpenCV-native comparison is not asserted to be pixel-identical to Chrome's decoder.

## 5. Verification evidence

| Check | Result |
|---|---|
| Pytest | **160 passed, 1 skipped** |
| Skipped test | Real checkpoint/model-contract test: external AI4Bharat checkout and checkpoint required |
| Python compileall | PASS |
| Node syntax checks | PASS for `app.js`, `landmark_diagnostics.js`, `landmark_worker.js` |
| Browser capture server/evaluator/manifest CLI help | PASS |
| Preflight | Executed; correctly identifies unavailable external assets and MediaPipe/Transformers/Paho |
| Phase 1 full validation | NOT RUN; blocked by missing external assets |
| Windows Python 3.12 | NOT RUN |
| Webcam/GPU/broker/firmware/hardware | NOT RUN |

Review environment was Linux, Python 3.13.5, pytest 9.0.2, Torch 2.10.0+cpu, OpenCV 4.13.0.92 and NumPy 2.3.5. MediaPipe, Transformers and Paho MQTT were absent. This does not match the target Windows Python 3.12 environment.

## 6. External blockers

- Pinned AI4Bharat Git clone at `df348f4a640060d4d39f02556d9f96851e8c46c7`.
- Exact locked checkpoint with SHA-256 `8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4`.
- All 77 original official validation video files.
- Windows Python 3.12 environment with MediaPipe/model/evaluation dependencies.
- Historical generator source/version metadata for legacy extractor parity.
- Separately labelled webcam recordings for the user-capture phase.

## 7. Release disposition

**Phase 0:** GO / complete.  
**Phase 1 tooling:** GO / implementation ready.  
**Phase 1 results:** BLOCKED / not run.  
**Recognition demonstration release:** NO-GO pending model, validation, target Windows and webcam evidence.  
**IoT:** NO-GO / hard locked.

See `DECISION_AND_GO_NO_GO.md` for the gate-by-gate decision and `PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md` for the exact execution sequence.

---

# 7 November 2026 — Phase 1 completion and Phase 2 addendum

This addendum supersedes the earlier verification table above where it stated that Phase 1 was not run and that the checkpoint test was skipped. Those statements describe the initial Linux review before the user supplied and executed the external assets.

## Verified target-Windows evidence

- Python 3.12.10 x64 virtual environment created.
- AI4Bharat repository commit and exact deterministic-inference dropout-only worktree change verified.
- Locked checkpoint SHA-256 verified; strict checkpoint load test passed.
- Missing upstream dependency `xgboost` identified from `models/__init__.py` and installed/pinned as `xgboost==3.4.1`.
- Windows regression suite after dependency repair: **161 passed, 0 failed**, one non-fatal Starlette/httpx deprecation warning.
- Official validation manifest verified: 77 items, 50 represented classes; all 77 source videos resolved.
- Browser-native capture completed and persisted: 77/77 clips.
- Paired browser Canvas/OpenCV reference evaluation completed: 77/77 clips.

## Phase 1 results

Browser Canvas Top-1 = **44/77 (57.14%)**, Top-5 = 67/77 (87.01%), macro F1 = 0.5207. OpenCV native reference Top-1 = **46/77 (59.74%)**, Top-5 = 69/77 (89.61%), macro F1 = 0.5594. Top-1 predictions agree on 66/77 clips. The two paths are both wrong on 30 clips, indicating that browser transport alone does not explain the majority of recognition errors. Browser feature extraction recorded 4,753/4,753 frames and zero extraction failures; OpenCV recorded 26 failed extractions among 4,753 source frames.

The validation set has low per-class support (23 singleton classes and 27 two-example classes). Threshold 0.40 remains uncalibrated. No threshold was changed.

## Phase 2 engineering work

- Inspected the exact pinned upstream generator and dataset source. It uses Pose `upper_body_only=True`, but upstream requirements do not pin the MediaPipe version/build used at training time.
- Confirmed the current application uses MediaPipe 0.10.21 full-body Pose and selects the first 25 points. This is slot/index compatibility, not historical detector parity.
- Added the Phase 1 evidence analysis bundle and report figures.
- Added a resumable all-77 four-path provenance runner: application native, OpenCV 640px PNG, OpenCV 640px JPEG q92, and pinned upstream generator under the modern first-25 compatibility adapter.
- Added strict repo/checkpoint/split/source/runtime fingerprinting, exact upstream dropout patch verification, per-clip output validation, resumability and IoT-off checks.
- Excluded the obsolete q72 condition and corrected naming so OpenCV q92 approximation is not described as actual browser Canvas.

The new Phase 2 runner has not yet been executed across all 77 videos on the target Windows machine. It is ready for that local execution. No recognition application behavior, checkpoint, model architecture, class order, 134-D feature representation, 169-frame input or IoT behavior was changed.

## Updated verification

- Target Windows base regression after xgboost repair: **161 passed, 0 failed**.
- Review-container suite after Phase 2 code/tests: **166 passed, 1 skipped** (Python 3.13.5; the remaining skip is an external-asset-dependent test in this review environment).
- Python compileall and Phase 2 CLI help checks: PASS.
- Phase 2 all-77 controlled ablation on target Windows: PENDING.

## Updated release decision

Phase 0: GO / complete. Phase 1: GO / 77-item validation baseline completed. Phase 2 analysis/tooling: GO / implemented. Phase 2 all-77 Windows execution: pending. Recognition demonstration release: NO-GO until Phase 2 and user webcam validation are completed. IoT: NO-GO / remains disabled.

---

## Superseding Phase 1 / Phase 2 update — 7 November 2026

The target Windows run subsequently completed the model strict-load test, the official 77-video browser capture, and paired Phase 1 evaluation. The actual target-Windows evidence is: strict checkpoint load **1 passed**; pre-Phase-2 regression suite **161 passed, 0 failed**; browser Canvas top-1 **44/77 (57.14%)**, top-5 **67/77 (87.01%)**, macro F1 **0.5207**; OpenCV native reference top-1 **46/77 (59.74%)**, top-5 **69/77 (89.61%)**, macro F1 **0.5594**. The original 160-pass Linux audit values above are retained as historical evidence and are not the latest target Windows results.

Phase 2 source/provenance analysis, resumable runner, report builder and review tests are included in the current candidate. Its all-77 target-Windows execution is still pending; do not claim Phase 2 complete until all 77 clip reports succeed. No recognition behavior has yet been changed. The release remains NO-GO for a validated recognition demonstration, and IoT remains disabled.
