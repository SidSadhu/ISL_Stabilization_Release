# Changelog — ISL Recognition Project

All changes from the baseline `ISL_PreIoT_Production_Release.zip` are documented here.

## Phase 2 candidate update — 2026-11-07 (engineering candidate; product behavior unchanged)

- Completed and retained the target-Windows official 77-clip browser Canvas capture and paired Browser/OpenCV baseline.
- Recorded browser top-1 44/77 (57.14%), top-5 67/77, macro F1 0.5207; OpenCV native top-1 46/77 (59.74%), top-5 69/77, macro F1 0.5594.
- Added a resumable all-77 feature-provenance ablation with native, 640px lossless PNG, 640px JPEG q92, and pinned upstream modern first-25 adapter paths.
- Added fail-closed provenance fingerprints, per-clip reports/logs, aggregate report builder and Phase 2 regression tests.
- Historical MediaPipe `upper_body_only=True` detector parity remains unproven. The modern adapter is not represented as exact historical parity.
- No model/checkpoint/threshold/50-class order/134-D/169-frame/IoT changes. All-77 Phase 2 execution on target Windows remains pending; recognition-demo release remains NO-GO.

---

## v1.0.0-rc3 — 2026-11-06 (Manual INCLUDE-50 implementation package)

### Product scope
- Manual Start/Stop capture of one isolated sign per inference is the MVP workflow.
- The full 50-class INCLUDE-50 classifier and its existing 134-D/169-frame contract are retained.
- Every accepted class remains eligible for text display; exactly two owner-selected labels may later be routed to IoT.
- Automatic sign segmentation and continuous sentence translation remain future extensions.

### IoT safety and configuration
- Removed implicit historical label-to-command mappings. `config/iot_label_map.json` is empty by default.
- Future configuration must contain exactly two official INCLUDE-50 labels and supported commands.
- The backend has a separate provisional 0.70 IoT confidence gate; the 0.40 text threshold remains uncalibrated.
- Firmware now requires two explicit compile-time label/command pairs; empty defaults reject commands and `LED_GPIO` remains disabled.
- Pre-IoT application lockdown remains hard-coded on.

### Evaluation and reproducibility
- Added exact official validation split manifest generation with pinned Git commit, SHA256, path count and label validation.
- Added a downloader profile that fetches only missing official validation items; no test samples are selected.
- Evaluation defaults to validation, checks the manifest against the pinned official list, reports top-1/top-5, per-class metrics, risk/coverage and latency, and marks incomplete runs.
- Test evaluation requires an explicit safety flag and is reserved for the frozen final model.
- Persistent backend storage, the simulator and ESP32 firmware now deduplicate by both command ID and capture ID. A separate backend capture-ID ledger survives display-history reset and retention; recognition-event upserts cannot cascade-delete it. The ESP32 NVS ring pointer is bounds-checked. This provides at-most-once protection, not exactly-once delivery.
- Removed the unused FPS environment setting and corrected Windows setup/evaluation instructions to avoid changing the text threshold or accidentally targeting test data.

### Verification boundary
- Isolated work-copy tests: 125 passed, 1 skipped (model assets unavailable). Python compilation and JavaScript syntax checks passed.
- Full validation accuracy, target Windows webcam behaviour, broker interoperability, firmware compilation and physical hardware are not claimed. See `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_WORKLOG.md`.

---

## v1.0.0-rc2 — 2026-10-29 (Historical baseline; subsequent correction applies)

> The rc2 pose-equivalence claim below was withdrawn in the 6 November rc3 audit. The modern first-25 slice preserves the checkpoint's feature slots but does not prove coordinate-level parity with the historical `upper_body_only=True` detector. MediaPipe removed that argument in v0.8.4, not v0.8.8. See `docs/KNOWN_LIMITATIONS.md` and `docs/REFERENCE_PIPELINE_PARITY_COMPAT_FIX.md`.

### P0 Fixes (historical decisions; see correction above)

**Pose preprocessing — BlazePose-33 upper-25 semantic mapping (landmarks.py)**
- `extract_upper_body_pose()` explicitly selects BlazePose-33 indices 0–24 with documented rationale
- Added `UPPER_BODY_INDICES`, `LEFT_WRIST_IDX`, `RIGHT_WRIST_IDX` as named constants
- Added module-level docstring explaining why `[:25]` is SEMANTICALLY CORRECT, not merely shape-compatible
- BlazePose-33 indices 0–24 ARE the upper-body landmarks (nose through hips); indices 25–32 are lower-body
- This faithfully reproduces `upper_body_only=True` semantics without pinning a legacy MediaPipe version

**Confidence threshold — lowered to 0.40 (provisional, UNCALIBRATED)**
- Default `ISL_CONFIDENCE_THRESHOLD` changed from 0.70 to 0.40
- At 0.70: 83% of correct reference clips were rejected (unusable for demos)
- At 0.40: 52% rejected; 14/14 accepted clips correct on the 29-clip exploratory sweep
- ALL results still labelled UNCALIBRATED — not derived from independent validation data
- UI shows prominent UNCALIBRATED badge on score display and footer

**Confidence UI — rejected predictions now visible, not hidden**
- `renderResult()` in app.js shows rejected candidate + score + reason
- "Candidate: X · Score 35.2% · Below threshold" — not a black box rejection
- All top-5 candidates always displayed regardless of accept/reject
- `confidence_hint` element shows rejection reason inline below score bar

**Hand coverage gate — relaxed from 50% to 30%**
- `ISL_MIN_HAND_RATIO` default: 0.50 → 0.30
- Many valid ISL signs use one hand and may appear for only part of a 6-second clip
- Still required for valid capture; just more realistic

### Improvements

**feature_extractor.py**
- Added `np.frombuffer` (zero-copy) in place of `np.fromstring`
- Explicit `model_complexity=1` to prevent regression if MediaPipe changes default
- Improved docstrings on performance decisions and session reuse

**app_config.py**
- `pose_compat_mode = "blazepose33_upper25_semantic"` — inspectable at runtime
- Docstring on confidence threshold with benchmark evidence and honest uncertainty

**Static files**
- `index.html`: UNCALIBRATED badge on score area and footer, cache-bust v2
- `styles.css`: `.badge-uncal` amber badge style, `.confidence-hint` italic warning
- `app.js`: stores threshold from health, shows richer label on rejected predictions

### New Tests

- `tests/test_landmarks_pose_compat.py` — 9 tests for pose semantic mapping (all PASS)
- `tests/test_confidence_policy.py` — 5 tests for threshold policy (all PASS)
- `tests/test_preprocessing_contract.py` — 16 tests for model input contract (all PASS)

### Documentation

- `docs/ARCHITECTURE_DECISION.md` — full decision record: model, pose compat, threshold, extraction
- `docs/KNOWN_LIMITATIONS.md` — categorised P0/P1 limitations with honest status
- `docs/TEST_REPORT.md` — test execution summary with skip reasons

## v1.0.0-pre-iot.1 — 2026-10-29 (Baseline from handoff)

Previous release candidate. See `COMPLETION_REPORT.md` in that ZIP for details.
Key features: record-then-extract pipeline, WebSocket capture, SQLite history,
FastAPI backend, live landmark preview WebSocket, IoT lockdown, 50-class label map.

## 2026-11-01 — Source-parity qualification correction

A review against the pinned upstream INCLUDE `generate_keypoints.py` found that the v1.0.0-rc2 wording overstated MediaPipe Pose parity. The app's modern full-body Pose output sliced to indices 0–24 preserves the expected 25 upper-body feature slots, but the modern Pose model is not demonstrated to produce the same coordinates as the legacy `upper_body_only=True` model. Source comments and `docs/KNOWN_LIMITATIONS.md` are corrected to state this explicitly. No extraction algorithm, model weight, preprocessing, threshold, or IoT behavior was changed by this correction.


## 2026-11-07 — RC3 stabilization audit addendum

- Added the missing safe `.env.example` and corrected the Starlette TestClient development dependency from `httpx2` to `httpx`.
- Aligned Python package metadata with `1.0.0-rc3`, removed stale generated egg-info and regenerated release SHA-256 checksums.
- Added a Phase 0 source audit and Phase 1 browser-native 77-clip capture/evaluation workflow.
- Added evaluator-side fail-closed IoT settings/mapping checks, exact upstream dropout diff verification, capture receiver resume/overwrite controls, and consolidated Windows release runner.
- Review-container verification: 160 passed, 1 skipped; Phase 1 model evaluation remains blocked until the pinned source checkout, checkpoint and 77 official videos are supplied on the target Windows environment.


## v1.0.0-rc3 — Phase 1 results and Phase 2 engineering addendum (2026-11-07)

### Verified on the target Windows environment
- Python 3.12.10 x64 environment created; AI4Bharat pinned commit and the exact deterministic-inference dropout patch verified.
- Official checkpoint SHA-256 verified and strict model loading passed.
- XGBoost 3.4.1 added to `requirements-model.txt` after the actual checkpoint test exposed the upstream `models/__init__.py` unconditional XGBoost import.
- Windows regression suite: 161 passed, 0 failed (one non-fatal Starlette/httpx deprecation warning).
- Official INCLUDE-50 validation manifest verified: 77 clips, all 50 classes, exact split-list SHA-256.
- Browser-native Canvas capture completed for all 77 validation videos.
- Paired browser/OpenCV validation evaluation completed for all 77 clips. Browser Canvas Top-1: 44/77 (57.14%); OpenCV native reference Top-1: 46/77 (59.74%). These are validation baseline results, not production-readiness claims.

### Phase 2 tooling and analysis
- Added the Phase 1 root-cause report, historical feature-generator provenance audit, per-clip disagreement analysis and five diagnostic figures; retained the original Windows Phase 1 metrics, per-clip CSV and run logs.
- Added a resumable all-77 four-path feature-provenance runner: application native frames, OpenCV-resized PNG, OpenCV-resized JPEG quality 0.92, and pinned upstream generator under the explicitly non-historical modern MediaPipe first-25 adapter.
- Added source/runtime/video/checkpoint fingerprints, exact upstream dropout-patch checks, strict pre-IoT guards and per-clip logs/reports.
- Removed the obsolete q72 condition from the Phase 2 campaign; the q92 comparison is explicitly labelled an OpenCV approximation, not actual browser Canvas.
- Historical MediaPipe training version remains unverified because the pinned upstream requirements do not specify it. No guessed legacy wheel was installed.
- The Phase 2 four-path 77-video execution on the target Windows machine remains pending. No model, checkpoint, class order, 134-D feature contract, 169-frame sequence, confidence threshold or IoT behavior was changed.

### Verification in this review work copy
- Python compileall: PASS.
- Phase 2-focused tests: 8 passed.
- Full suite after Phase 2 additions: 168 passed, 1 skipped (review container, Python 3.13.5).
- Target Windows must rerun the release suite after updating to this Phase 2 package before its run is considered the new Windows test record.


## 2026-11-07 — Phase 2 missing-Pose diagnostic recovery

- Diagnosed the 76/77 Windows Phase 2 run failure to the pinned upstream keypoint generator dereferencing `pose_landmarks=None` on `Adjectives/79. short/MVI_9302.MOV`.
- Added a diagnostic-only 25-NaN Pose sentinel that follows the upstream dataset's missing-observation interpolation convention; the pinned AI4Bharat checkout and production app are unchanged.
- Added a strictly fingerprint-gated resume migration for the exact known previous 76 successful clip reports, plus regression coverage for missing-Pose handling and compatibility rejection.
- Review-container full suite after the recovery patch: 172 passed, 1 skipped. Target-Windows rerun and 77/77 Phase 2 acceptance remain pending.
