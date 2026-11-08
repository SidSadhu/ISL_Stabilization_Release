# Completion Report — 6 November 2026 Implementation Package

## 1. What was delivered

This package implements the agreed scope in the isolated working copy:

- One manually captured isolated sign per Start/Stop cycle.
- Full AI4Bharat INCLUDE-50 class vocabulary retained.
- Existing 134-D frame features and 169-frame model input contract retained.
- Text recognition remains independent of IoT routing.
- IoT configuration defaults to zero mappings; a future release requires exactly two official labels selected by the project owner.
- Application-level pre-IoT lockdown remains enabled.
- Official 77-item validation-list parser, missing-only validation downloader and validation-first evaluator added.

## 2. Exact software changes

See [`IMPLEMENTATION_WORKLOG.md`](IMPLEMENTATION_WORKLOG.md) for the file-level work summary and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for phases and acceptance criteria. Major changes include explicit IoT allowlist loading, fail-closed command checks, separate IoT confidence gate, simulator configuration checks, backend/SQLite/simulator/firmware capture-level deduplication, a permanent backend anti-replay ledger that survives history reset/retention, official validation split verification, test-split lock and updated Windows handoff scripts.

The browser's JPEG quality was provisionally changed from 0.72 to 0.92 at the existing 640px width. This is an evidence-led capture-fidelity experiment setting, not a demonstrated accuracy improvement.

## 3. Verification actually run in this container

| Check | Result | Boundary |
|---|---|---|
| `PYTHONPATH=src:scripts:. python -m pytest -q -rs --disable-warnings` | **160 passed, 1 skipped** | One model-contract test skipped because the external AI4Bharat clone/checkpoint are unavailable. |
| `python -m compileall -q src scripts tests` | **Passed** | Python syntax/bytecode compilation only. |
| `node --check` on active browser JavaScript files | **Passed** | Node `--check` passed for `app.js`, `landmark_diagnostics.js` and `landmark_worker.js`; syntax only. |
| Official validation path-list parser | **77 unique paths / 50 labels; SHA256 matched** | Parsed the exported upstream split-list bytes. The exported repo did not contain `.git` metadata, so local HEAD verification still must pass. |
| `scripts/preflight.py` | **Ran and reported blockers** | Correctly identified missing MediaPipe, Transformers, Paho MQTT, AI4Bharat checkout and checkpoint in this container. |
| PowerShell scripts | **Not executed** | No PowerShell runtime is available in this Linux container. |
| ESP32 firmware | **Not compiled** | No Arduino CLI/toolchain/board profile is available. |

## 4. Work deliberately not claimed as complete

- No full 77-video validation inference or recognition accuracy is reported.
- No threshold calibration is claimed. The text gate `.40` remains uncalibrated; IoT gate `.70` is provisional.
- No actual Windows Python 3.12 `.venv`, PyTorch/CUDA or MediaPipe runtime execution was performed.
- No webcam end-to-end recognition, capture latency or target-laptop performance is claimed.
- No live Mosquitto/Paho integration or publish/ack interoperability is claimed.
- No ESP32 compilation, wiring or physical LED/buzzer action is claimed.
- No external signer or regional sign-variation generalisation is claimed.

## 5. Required next local milestones

1. Extract the ZIP into a separate folder. Run `scripts/phase0_backup_and_git.ps1 -ProjectRoot <ACTIVE_PROJECT_PATH>` from the extracted package to back up the real current project before merging.
2. Use the existing Windows Python 3.12 environment and run preflight plus the regression suite.
3. Verify checkpoint SHA256, strict checkpoint loading and deterministic repeat-inference on the target laptop.
4. Run the official validation manifest/download/evaluation workflow until `evaluation_complete_for_official_split` is true.
5. Use those results to decide whether the baseline preprocessing, detector, capture fidelity or model needs further change.
6. Only after recognition and simulation gates, choose two IoT labels, configure backend and firmware allowlists, review broker security, compile firmware and run a supervised low-voltage hardware test.

## 6. Final status

The deliverable is an implementation package and an executable local validation plan, **not a certified production model or finished physical IoT product**. The most important next evidence is the complete official 77-example validation run using the user's actual checkpoint and Windows environment.


## 7 November 2026 source-audit and stabilization addendum

The latest clean working release has 160 passed and 1 skipped in the Linux/Python 3.13.5 review container. The single skip requires the external AI4Bharat checkout and checkpoint. Phase 0 reconciliation and the Phase 1 browser-native capture/evaluation tooling are documented in `engineering/PHASE0_SOURCE_AUDIT.md` and `engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md`. The full 77-video inference benchmark has **not** been run because the external repository, checkpoint and video files are absent from the supplied archives. The target Windows Python 3.12 runtime and webcam remain unverified.

---

## 7 November 2026 — target-Windows evidence addendum

The previous completion statement predates the user's local execution of Phase 1. Since then, the target Windows environment has verified the strict checkpoint load, completed 161 passing regression tests, verified the official 77-video validation manifest, captured 77/77 browser-native clips and completed the paired browser/OpenCV evaluation. The metrics and per-clip results are retained in `engineering/phase1_evidence/`.

Phase 1 baseline: Browser Canvas Top-1 44/77 (57.14%), Top-5 67/77 (87.01%), macro F1 0.5207. OpenCV native reference Top-1 46/77 (59.74%), Top-5 69/77 (89.61%), macro F1 0.5594. This is a small validation set, not an unbiased final release result.

Phase 2 historical feature-provenance audit, report and all-77 four-path diagnostic runner have been added. The runner compares app-native, OpenCV-resized PNG, OpenCV-resized JPEG q92 and upstream modern-adapter paths. Actual all-77 Phase 2 execution on the Windows machine remains pending. Historical MediaPipe training-time version/build remains unverified. The checkpoint and recognition contract remain unchanged; IoT remains disabled.

---

## 6. Superseding Phase 1 / Phase 2 campaign status — 7 November 2026

The earlier section 3–5 above is the original package-build snapshot and is superseded where it stated that target-Windows validation had not yet run. The user subsequently supplied target-Windows evidence:

- Official 77-video / 50-class validation list verified against pinned commit and split SHA-256; all 77 clips found locally.
- Browser Canvas capture completed 77/77; the first 14 captures were preserved while resuming at clip 15.
- Strict checkpoint load passed (1 passed); target Windows pre-Phase-2 regression suite: 161 passed, 0 failed.
- Browser Canvas top-1 44/77, top-5 67/77, macro F1 0.5207; OpenCV native reference top-1 46/77, top-5 69/77, macro F1 0.5594.

The Phase 2 source-analysis and resumable four-path all-77 runner are now included. The target-Windows Phase 2 execution remains pending. This package is an engineering candidate, not a verified recognition-demo release. The test split remains locked and IoT remains disabled.
