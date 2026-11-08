# Superseding execution update — 7 November 2026

This update supersedes earlier pending-status statements in the historical snapshot below.

| Component | Current evidence |
|---|---|
| INCLUDE-50 manifest | 77/77 official validation entries, 50/50 classes, pinned split hash verified |
| Source videos | 77/77 resolved locally; no additional downloads required |
| Browser-native capture | 77/77 captured, including resumed clip 15; capture log retained |
| Official checkpoint | SHA-256 verified; strict-load test passed on target Windows |
| Target Windows regression before Phase 2 | 161 passed, 0 failed |
| Phase 1 paired recognition | Complete: browser 44/77 top-1; OpenCV native 46/77 top-1 |
| Phase 2 source/diagnostic tooling | Implemented; review suite 168 passed, 1 skipped |
| Phase 2 all-77 ablation | Not yet executed on target Windows; next gate |
| Historical upper_body_only detector parity | Unproven; modern first-25 adapter is not claimed equivalent |
| IoT | Disabled, empty mapping, hard pre-IoT lock true |

The Phase 1 metrics are validation-set evidence only. The 0.40 text threshold remains uncalibrated; the test split is untouched. Phase 2 must complete 77/77 before any recognition-code remediation is proposed. The historical status snapshot below is retained for audit history and is superseded where it conflicts with this dated update.

---

# Implementation Status — 7 November 2026

Status labels refer to this source package and are not a claim of target-Windows or hardware readiness.

| Component | Status | Evidence / boundary |
|---|---|---|
| Product scope | `locked` | One manually bounded isolated sign per capture; no automatic segmentation or continuous translation in the MVP. |
| Official INCLUDE-50 vocabulary | `tested` | Bundled official map contains 50 classes and stable index order; model/checkpoint smoke test still needs the external assets. |
| Feature contract | `tested` | Unit tests cover 134-D finite float input and the 169-frame sequence contract. |
| Browser camera UI | `implemented; target test pending` | Manual Start/Stop controls and WebSocket client exist; actual Windows browser/webcam behaviour is not verified here. |
| Primary frame transport | `implemented; profiling pending` | The app buffers JPEG frames in memory during capture and extracts them sequentially after Stop. It is not an asynchronous bounded frame queue. |
| Landmark preview | `implemented; diagnostic only` | Separate preview route/session; its visual output is not the classifier input and may add CPU load. |
| MediaPipe Hands/Pose extractor | `implemented; external runtime blocked` | Existing extractor retains 134-D layout. MediaPipe is not installed in this container. Modern Pose coordinate parity with historical `upper_body_only=True` is unproven. |
| Capture-quality gates | `tested with test doubles` | Tests exercise capture limits and quality checks; production thresholds are provisional and need measured webcam/validation review. |
| Official Transformer adapter | `implemented; checkpoint smoke test pending` | Strict-load path is present. The official AI4Bharat checkout and checkpoint are not in this container. |
| Actual model inference | `blocked by external assets` | No full INCLUDE-50 inference or accuracy result is claimed here. |
| Text output and recognition history | `implemented; API tests pass` | Text result and SQLite history remain separate from IoT. |
| IoT mapping configuration | `implemented; disabled by default` | Empty JSON mapping; loader accepts zero or exactly two official labels; malformed settings fail closed. The project owner has not selected the two labels. |
| IoT command policy | `tested` | Accepted result, allowlist, independent provisional 0.70 actuator confidence gate, cooldown and pre-IoT lockdown checks are covered by tests. A durable capture-ID ledger survives history reset/retention. |
| MQTT simulator logic | `tested without live broker` | Deterministic command/ack validation and duplicates are tested in-process. Paho/Mosquitto interoperability is not verified here. |
| ESP32 firmware | `updated; compile pending` | Empty compile-time allowlist rejects commands; GPIO default is -1; NVS stores command/capture IDs in a bounded 64-entry ring and guards its persisted pointer. Arduino toolchain/board compilation and hardware testing were unavailable. |
| Browser-native Phase 1 tooling | `implemented; asset-gated` | Loopback-only Canvas capture page and paired evaluator are present; the exact 77-clip run has not been executed because model/video assets are absent. See `engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md`. |
| Official validation manifest | `implemented; source-list hash verified` | Parser validates 77 unique paths and all 50 classes against the pinned path-list SHA256. The user's local Git checkout must pass the pinned HEAD check. |
| Validation downloader | `implemented; live download pending` | `validation-all` selects only missing official validation videos; it never selects test rows. Network/data download not run in this container. |
| Dataset evaluator | `implemented; real inference pending` | Defaults to validation; verifies official manifest, reports top-1/top-5, per-class metrics, confidence risk/coverage and latency; test requires an explicit flag. |
| Regression suite | `review-container regression verified` | 160 passed, 1 skipped in the 7 Nov 2026 source audit. The skipped test requires the external AI4Bharat checkout and model assets. This is not target-Windows or real-model evidence. |
| Python compilation | `passed` | `python -m compileall -q src scripts tests`. |
| JavaScript syntax | `passed` | Node syntax checks completed for active frontend JS files. |
| Windows Python 3.12 / CUDA | `not tested here` | Current work container was Linux/Python 3.13.5; target machine and GPU are unavailable. |
| External signer generalisation | `not tested` | Needs a separately labelled multi-signer evaluation set and regional-variation review. |
| Physical ESP32/LED | `not tested; locked` | Requires chosen labels, simulator/broker verification, firmware compile, electrical review and supervised low-voltage test. |

## Verification summary

The latest source audit on 7 November 2026 reports **160 passed, 1 skipped** in the review container (Python 3.13.5 / pytest 9.0.2). The single skip is the checkpoint-dependent model-contract test. Python compileall and active JavaScript syntax checks passed. This review environment does not match the supported Windows Python 3.12 target and lacks the AI4Bharat checkout, checkpoint and MediaPipe runtime. See `engineering/PHASE0_SOURCE_AUDIT.md` for exact environment and limitations.

These results demonstrate software-level regression coverage only. They do not establish full 77-video accuracy, target-laptop performance, real webcam recognition, broker interoperability, firmware compilation or physical actuation. See [`engineering/PHASE0_SOURCE_AUDIT.md`](PHASE0_SOURCE_AUDIT.md), [`engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md`](PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md), [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md), [`IMPLEMENTATION_WORKLOG.md`](../IMPLEMENTATION_WORKLOG.md), [`RELEASE_MANIFEST.json`](../RELEASE_MANIFEST.json), and [`engineering/PHASE0_SOURCE_AUDIT.md`](../engineering/PHASE0_SOURCE_AUDIT.md).

## Superseded implementation paths

- The old raw text MQTT publisher on `isl/stable_word` is not used by the web application; protocol v1.0 is the supported command/ack path.
- Streamlit is superseded by plain HTML/JavaScript + FastAPI + WebSocket.
- The experimental continuous/sliding-window runner is not part of the manual one-capture/one-inference MVP.
- Historical notes describing one preselected `hello` IoT mapping or a bounded asynchronous queue are superseded by the current product scope and audited primary capture implementation.
