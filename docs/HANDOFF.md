# Current handoff update — 7 November 2026

**Do not restart Phase 0 or Phase 1.** Both are complete with user-provided target-Windows evidence.

- Python 3.12.10 x64 environment was created and the upstream import omission was corrected by pinning `xgboost==3.4.1` in `requirements-model.txt`.
- Official checkpoint strict-load test: 1 passed. Full pre-Phase-2 Windows suite: 161 passed, 0 failed.
- Official INCLUDE-50 validation manifest: 77 clips / 50 labels, exact pinned split hash. All 77 videos resolved locally.
- Browser Canvas capture: 77/77 received; log and capture index retained.
- Paired Phase 1 inference: browser top-1 44/77 (57.14%); OpenCV native 46/77 (59.74%).
- Phase 2 source/provenance diagnostics and resumable runner are now included in this candidate. The all-77 Phase 2 ablation is **not yet run** on target Windows.

**Next action:** overlay this Phase 2 candidate into the existing project while preserving `.venv`, `.env`, `outputs/`, and local assets; rerun checksum/dependency/tests; then follow `engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md`. The runner is resumable; console history is not the record of truth. Attach `summary.json`, `per_clip_summary.csv` and `report/PHASE2_ABLATION_REPORT.md` when complete.

IoT remains disabled. Keep the checkpoint, class order, 134-D contract, 169-frame input, 0.40 uncalibrated text threshold, and test split unchanged.

---

# Engineering Handoff — 6 November 2026

## Current deliverable

This package implements the agreed manual isolated-sign product scope around the existing INCLUDE-50 model contract. It includes the existing FastAPI/browser application, the strict IoT mapping layer, official validation-manifest tooling, an evaluation runner, tests and Windows-first scripts. The product recognises one sign per manually started/stopped capture. It is not continuous sign-language translation.

## Product decisions

- Retain the full 50-class INCLUDE-50 recogniser, official class ordering, 134-D feature vector and 169-frame model sequence.
- Use one manual Start/Stop capture per isolated sign. Automatic gesture segmentation is deferred.
- Display accepted recognition results as text. Text recognition and hardware actuation are separate decisions.
- The IoT map is empty by default. The project owner must select exactly two official labels for a future IoT release; no labels are selected in this package.
- The pre-IoT application code lock remains enabled. The firmware allowlist is empty by default and `LED_GPIO=-1`. Backend capture-ID idempotency is stored in a private SQLite ledger separate from visible IoT history, so history reset and retention do not permit replays.

## What was changed in this implementation package

- Replaced implicit historical label-to-command defaults with an explicit JSON allowlist loader.
- Added separate provisional IoT confidence threshold, map-health reporting, enable guard and fail-closed command path.
- Made simulator startup require exactly two valid mappings.
- Updated ESP32 sketch to enforce the same two explicit label/command pairs; empty firmware defaults reject commands.
- Changed browser JPEG quality to a provisional 0.92 at 640px; this is not an accuracy claim.
- Added exact pinned 77-item official validation manifest preparation and a missing-only downloader profile.
- Added validation-first evaluation with full-split verification, top-1/top-5, class metrics, risk/coverage and latency. Test split is locked unless explicitly enabled for a final frozen evaluation.
- Added Phase 0 backup/Git and a Windows validation workflow.

## Important current implementation detail

The primary webapp sends JPEG frames over `/ws/capture` and buffers them for the active manual capture. It then performs sequential MediaPipe extraction after Stop and runs one inference. The primary path does **not** currently use the bounded asynchronous frame queue described in some earlier documents. The separate `/ws/landmark-preview` path is diagnostic only and is not the classifier input.

## Verification in the isolated work copy

- `pytest`: **160 passed, 1 skipped** in the 7 Nov 2026 review-container audit (Python 3.13.5 / pytest 9.0.2; not target Windows Python 3.12). The skip is the official model-contract test, which requires the external AI4Bharat clone and checkpoint.
- Python source compilation: passed.
- JavaScript syntax checks: passed.
- Preflight: command ran and correctly reported unavailable external runtime assets/dependencies in this container.
- The exact official validation path list was parsed as 77 unique items spanning all 50 classes; the exported snapshot lacked `.git` metadata, so the user's complete clone must pass the pinned Git HEAD guard.

## Remaining external gates

1. On Windows, run `scripts/phase0_backup_and_git.ps1` before applying the package to an existing working project.
2. Use the existing Python 3.12 `.venv`; run `scripts/preflight.py` and `pytest`.
3. Verify strict checkpoint loading and deterministic repeated inference with the local official repo/checkpoint.
4. Generate the official validation manifest from the complete pinned AI4Bharat Git clone, download only missing validation examples, and evaluate all 77. Do not use test videos for tuning.
5. Run the browser camera flow end-to-end, measure frames, feature extraction, post-Stop latency and preview overhead; use the browser-native Phase 1 capture/evaluation scripts to capture the actual 640px/JPEG Q92 path across all 77 official validation clips. The legacy Q72 pilot scripts are not the full validation benchmark.
6. Perform paired MediaPipe feature/logit comparisons before altering extractor/version/preprocessing.
7. After the owner chooses two IoT labels, configure the JSON map and firmware macros consistently; then test Mosquitto + simulator positive and negative cases. Keep the app-level lock on until reviewed.
8. Compile the sketch for the exact ESP32 board, review low-voltage LED wiring, and only then perform supervised physical tests.
9. Test with signers not represented in the reference clips, including non-INCLUDE gestures and regional variants.

## Evidence boundary

This container does not include the user's Windows Python 3.12 environment, MediaPipe runtime, official checkpoint binary, local video bytes, webcam, broker or ESP32. No full validation accuracy, target-laptop latency, real webcam recognition, live MQTT interoperability, firmware compilation or physical actuation is claimed.


## 7 Nov 2026 stabilization audit addendum

- The latest clean source audit report is `engineering/PHASE0_SOURCE_AUDIT.md`.
- The Phase 1 browser-native capture and paired evaluation tooling is in `scripts/browser_capture_protocol.py`, `scripts/serve_browser_validation_capture.py`, `scripts/browser_validation_capture.html`, and `scripts/evaluate_browser_canvas_validation.py`.
- Use `engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md` and `scripts/run_phase1_browser_capture.ps1`; do not treat the old eight-clip transport pilot as the official 77-clip benchmark.
- The latest review-container suite is 160 passed, 1 skipped; the one skip requires the real external checkpoint/repository.
- `RUN_COMPLETE_ISL_STABILIZATION.ps1` is the consolidated Windows software check and can optionally execute Phase 1 after all 77 browser capture bundles exist.
- IoT remains hard locked, `ISL_IOT_ENABLED=false`, and the shipped map remains empty.
