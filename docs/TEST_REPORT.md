# Test Report — ISL Manual INCLUDE-50 Implementation Package

**Generated:** 7 November 2026  
**Environment:** Isolated Linux review container, Python 3.13.5, pytest 9.0.2. The repository requirement is pytest 8.x; this test run is not an installation verification for the pinned target environment and is not the target Windows Python 3.12 environment.

## Final regression result

Command:

```bash
PYTHONPATH=src:scripts:. python -m pytest -q -rs --disable-warnings
```

Result: **160 passed, 1 skipped** (clean stabilization working copy, re-run after Phase 1 tooling and the IoT-disabled evaluator guard were added).

The single skipped test is `tests/test_model_contract.py`, which requires `AI4BHARAT_REPO` and the actual external model assets. This is not a pass for checkpoint loading or inference. The test run used preinstalled review-container packages and does not prove a clean Windows Python 3.12 install.

## Additional checks

| Check | Result | Notes |
|---|---|---|
| Python compileall | PASS | `python -m compileall -q src scripts tests` |
| JavaScript syntax | PASS | Node `--check` on active frontend scripts `app.js`, `landmark_diagnostics.js` and `landmark_worker.js` |
| Official validation split-list parser | PASS | 77 unique paths, all 50 labels, expected SHA256; complete Git HEAD guard still requires local `.git` metadata |
| Preflight | Executed; external blockers reported | MediaPipe, Transformers, Paho MQTT, AI4Bharat clone and checkpoint absent in this container |
| PowerShell scripts | Not executed | No PowerShell runtime available here |
| Arduino firmware | Not compiled | No Arduino toolchain/board build context available |

## What these tests establish

- Core preprocessing, fixed model contract, label order, API behaviour, capture lifecycle, persistence, IoT protocol validation, fail-closed configuration, firmware allowlist source checks, validation-manifest safeguards, and test-split lock are covered at the tested software seams. New browser-capture protocol, IoT-off evaluator guard and capture-receiver resume regressions verify the Phase 1 diagnostic contract. Existing idempotency regressions verify that recognition upserts, event-history reset, retention pruning and legacy-database migration do not erase the backend anti-replay ledger.
- IoT unit tests use explicitly configured test fixtures; production config still ships with an empty mapping and code-level lockdown.
- Some model/API paths use fake model/extractor objects. Passing tests do not establish real model accuracy or webcam recognition quality.

## Not tested

- Strict load/inference using the user's checkpoint and AI4Bharat Git checkout.
- Full 77-video validation evaluation or any official accuracy score.
- Target Windows Python 3.12, CUDA/GPU, webcam capture and capture-to-text latency.
- Live Mosquitto/Paho publish/ack, ESP32 firmware compilation, GPIO wiring or physical LED/buzzer behaviour.
- External signer or regional ISL variation generalisation.

The review container package set included FastAPI 0.128.2, Starlette 0.50.0, NumPy 2.3.5, pandas 2.2.3, OpenCV 4.13.0.92 and Torch 2.10.0+cpu; MediaPipe and Transformers were absent. See [`engineering/PHASE0_SOURCE_AUDIT.md`](../engineering/PHASE0_SOURCE_AUDIT.md) for detailed findings and [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md), [`IMPLEMENTATION_WORKLOG.md`](../IMPLEMENTATION_WORKLOG.md), and [`COMPLETION_REPORT.md`](../COMPLETION_REPORT.md) for the acceptance sequence and limitations.

---

## Superseding execution evidence — 7 November 2026

The earlier 160-passed result above is a dated Phase 0 review snapshot, not the latest verification. Subsequent evidence:

- Target Windows 11 / Python 3.12.10 strict official checkpoint-load test: **1 passed**.
- Target Windows complete pre-Phase-2 regression suite: **161 passed, 0 failed** (one non-fatal Starlette/httpx deprecation warning).
- Phase 1 target Windows official validation inference: **77/77 clips completed**. Browser Canvas top-1 44/77, top-5 67/77, macro F1 0.5207; OpenCV native top-1 46/77, top-5 69/77, macro F1 0.5594.
- Current Phase 2 candidate review-container suite: **168 passed, 1 skipped** under Python 3.13.5; the skip requires the external upstream checkout/checkpoint. This is not a target-Windows run of the new Phase 2 candidate.

The all-77 Phase 2 provenance run remains pending on target Windows. See `../engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md`. Historical records above are retained for chronology.
