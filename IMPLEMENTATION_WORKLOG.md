# Implementation Worklog — 6 November 2026

## Scope applied

- Manual Start/Stop isolated-sign capture remains the product workflow.
- Full INCLUDE-50 classification remains intact.
- Text output remains available for all accepted classes.
- IoT is a separate, exact-two-label backend route. The two labels remain unselected and the shipped map is empty.
- Automatic segmentation and continuous sentence translation remain outside the MVP.

## Changes in this implementation package

1. Added `config/iot_label_map.json`, intentionally containing an empty mappings object.
2. Reworked `src/isl_recognition/iot_protocol.py` usage so commands require an explicit label-to-command mapping; no module-global default can actuate a class.
3. Added settings for IoT mapping path and independent minimum confidence.
4. Added runtime mapping loading, health reporting and enable guard; invalid/missing config fails closed while text recognition continues.
5. Changed IoT command generation to require accepted recognition, allowlist membership, and the separate IoT confidence minimum.
6. Updated simulator startup to require exactly two configured labels and pass the same map/threshold to its simulated endpoint.
7. Changed browser capture JPEG quality from 0.72 to provisional 0.92 at the existing 640px width. The source image remains unmirrored for classifier input.
8. Added pinned official 77-item validation manifest preparation with commit/hash/count/class-label checks.
9. Added a `validation-all` downloader profile that downloads only missing official validation items and never selects test examples.
10. Changed dataset evaluation default to validation; test requires an explicit safety flag. Added top-5 accuracy, risk/coverage summaries and latency statistics; partial runs are labelled incomplete.
11. Added `.env.example`, Phase 0 backup/Git script, full implementation plan, engineering decision record and this worklog.
12. Updated release manifest/changelog so the old pose-parity claim is not carried forward as a current verified fact.
13. Hardened validation-manifest Git-check errors so a source export without `.git` is reported as a clear blocker.
14. Updated test fixtures so hard-coded labels are only used in isolated tests, not as product default mappings.
15. Added persistent SQLite uniqueness for one IoT event per capture; repeated command requests using a new command ID are not published. Existing databases containing duplicate capture rows fail closed and require backup/audit rather than silent cleanup.
16. Added capture-ID duplicate rejection to the simulator and ESP32 Preferences-backed deduplication in addition to command-ID deduplication. Firmware verifies deduplication state is persisted before it permits GPIO output.
17. Added a database migration test proving existing duplicate capture commands cause an explicit startup failure requiring audit, not silent migration or duplicate actuation.
18. Removed the unused `ISL_TARGET_FPS` configuration to avoid implying that it changes the browser's fixed 40 ms send interval; documented the retained queue-size setting as compatibility-only.
19. Corrected the Windows setup guide's stale text threshold and replaced its invalid/unlocked test-split example with the exact official validation workflow.
20. Replaced recognition-event `INSERT OR REPLACE` with an in-place upsert so a repeated capture cannot cascade-delete an existing IoT event/dedup record.
21. Added a private SQLite `iot_capture_dedup` ledger, migrated existing IoT event rows into it, and made visible history reset/retention leave the anti-replay ledger intact.
22. Added regression tests for duplicate capture rejection after history reset, retention pruning, recognition upsert and legacy-database migration.
23. Added ESP32 NVS persisted-ring-pointer bounds recovery and a source-level regression check.
24. Added a webapp integration regression proving a capture cannot be issued a second IoT command after visible history has been reset.

## Verification performed in the isolated container copy

- `PYTHONPATH=src:scripts:. python -m pytest -q --disable-warnings` → **125 passed, 1 skipped** after capture-level deduplication tests were added.
- The official `include50_val.txt` source snapshot was SHA256-checked and parsed as 77 unique paths covering 50 labels. The work container's exported source snapshot does not contain the original Git metadata, so the repository-HEAD check was not performed against that extracted snapshot; the user's local Git clone must pass the script's pinned-commit check.
- Python compilation and JavaScript syntax checks are included in the final package verification. The ESP32 pointer bounds guard is source-tested only; firmware compilation remains unavailable here.

## Not verified here

- User's active Windows Python 3.12 `.venv` and real MediaPipe runtime.
- Strict checkpoint loading and full 77-video inference.
- Actual webcam/browser streaming and target-laptop latency.
- Live Mosquitto broker, MQTT acknowledgements, ESP32 firmware build, GPIO wiring or physical LED/buzzer.
- Recognition generalisation to new signers or regional sign variants.

## Safety note

The default IoT mapping is empty and code-level pre-IoT lockdown remains enabled. Do not edit `pre_iot_lockdown` to false or attach hardware until the validation and IoT release gates in `IMPLEMENTATION_PLAN.md` are completed.


## 7 November 2026 — Consolidated stabilization and Phase 1 tooling addendum

- Completed the Phase 0 audit of the supplied handover and source-only archives; see `engineering/PHASE0_SOURCE_AUDIT.md`.
- Reconciled the missing `.env.example`, Starlette TestClient dependency (`httpx` rather than `httpx2`), release version metadata and stale documentation/checksum artifacts.
- Added browser-native 640px / JPEG 0.92 Canvas capture protocol, loopback-only receiver, 77-item validation capture UI and paired feature/model evaluator. The evaluator checks the pinned upstream commit, exact documented dropout diff, locked checkpoint SHA256, official validation split and IoT-off configuration.
- Added explicit receiver resume/overwrite semantics and fail-closed duplicate-file handling.
- Added release checksum verifier and consolidated Windows runner `RUN_COMPLETE_ISL_STABILIZATION.ps1`; added `scripts/run_phase1_browser_capture.ps1`.
- Verification in the Linux/Python 3.13.5 review container: **160 passed, 1 skipped**; Python compilation and active JavaScript syntax checks passed; preflight correctly reports absent MediaPipe, Transformers, Paho MQTT, AI4Bharat checkout and checkpoint.
- The Phase 1 benchmark was not executed: repository, checkpoint and official validation videos are not included in the uploaded packages. No new recognition accuracy claim is made. No model, class order, 134-D feature contract, 169-frame input or IoT behavior was changed.

## 7 November 2026 — Phase 1 execution and Phase 2 campaign

- User Windows Python 3.12.10 x64 environment created and dependency stack installed.
- `xgboost==3.4.1` added to `requirements-model.txt` after the strict checkpoint test uncovered the unconditional import from upstream `models/__init__.py`.
- Strict locked checkpoint load passed; Windows regression suite passed 161 tests with one non-fatal deprecation warning.
- Official validation split: exact 77 items / 50 classes verified; all 77 local files resolved without downloading additional clips.
- Browser Canvas capture resumed from clip 15 after a transient sampling failure and completed at 77/77; all captures preserved in the user environment.
- Paired Phase 1 evaluation completed on all 77 clips. Original metrics/per-clip CSV and terminal/capture logs are retained in `engineering/phase1_evidence/`.
- Phase 1 analysis and visualisations generated under `engineering/phase2_evidence/`.
- Historical source inspection confirmed `upper_body_only=True` in upstream generator but no MediaPipe version pin in upstream requirements. Current 0.10.21 full Pose first-25 compatibility adapter is not described as historical parity.
- Added `run_phase2_feature_provenance_77.py` and `diagnose_browser_transport_stages.py` for resumable all-77 comparisons of native app, 640px PNG, JPEG q92 and upstream modern-adapter paths.
- Corrected the Phase 2 transport diagnostic to create only q92 (not q72), clarified the q92 OpenCV branch is an approximation and not actual Canvas, strengthened source/runtime hashes, exact dropout patch verification, output validation and resumability.
- Phase 2 tests added; full review-container suite: 166 passed, 1 skipped. The target-Windows Phase 2 all-77 run is still pending.
- Model, checkpoint, class order, 134-D features, 169 frames, thresholds and IoT behavior remain unchanged.

---

## 7 November 2026 — target Windows Phase 1 evidence and Phase 2 candidate

- User target Windows environment: Python 3.12.10 x64; upstream commit and exact dropout-only change verified; checkpoint SHA256 verified.
- Added `xgboost==3.4.1` to model requirements after the real model import chain required the upstream dependency.
- Strict checkpoint load passed; the complete pre-Phase-2 Windows test suite returned 161 passed, 0 failed.
- Verified official 77-item / 50-class validation manifest and 77/77 local source clips; browser Canvas capture completed 77/77.
- Completed paired browser/OpenCV Phase 1 evaluation: 44/77 and 46/77 top-1 respectively. Full source logs, metrics and per-clip table are retained in `engineering/phase1_evidence/`.
- Added Phase 2 resumable four-path feature provenance ablation (native, resized PNG, resized JPEG q92, upstream modern first-25 adapter), report builder and fail-closed preflight. Review-container tests: 168 passed, 1 skipped.
- Target-Windows Phase 2 all-77 ablation remains the next gate. No model, checkpoint, label order, feature contract, sequence length, threshold or IoT behavior has been changed.
