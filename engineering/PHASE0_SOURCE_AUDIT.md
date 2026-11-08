# Phase 0 — Source Audit & Handover Reconciliation

**Project:** ISL Recognition — INCLUDE-50 isolated-sign recognition  
**Audit date:** 7 November 2026  
**Implementation archive:** `ISL_Recognition_Source_Only(1).zip`  
**Decision record:** `ISL_ChatGPT_Handover_Package(1).zip`  
**Audit result:** Phase 0 complete; Phase 1 tools implemented, execution blocked by external assets.

## 1. Executive result

The handover archive was read in full before the application source was inspected. It contains nine files, including the project context, current state and evidence, decision register, experiment ledger, consolidated engineering plan, two handover prompts, package manifest and first-read guide. The application source archive contains 169 ZIP entries and passes ZIP CRC validation. The archive includes the `docs/`, `esp32/`, `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_WORKLOG.md` content that the handover says was absent from an earlier source export.

The current implementation is the `1.0.0-rc3` manual INCLUDE-50 application. It is not a continuous sign-language translator. The production app's primary path is manual Start/Stop: JPEG frames are buffered during one capture, then extracted sequentially after Stop, followed by one inference. `ISL_FRAME_QUEUE_SIZE` remains a compatibility setting; it does not indicate an active asynchronous queue.

No changes were made to the model architecture, checkpoint, 50-class order, feature layout, 169-frame preprocessing contract, inference threshold or production capture behavior during this audit. The hard pre-IoT application lock remains enabled and the shipped IoT label map remains empty.

## 2. Locked recognition and product contract verified in source

| Contract | Current implementation | Source evidence |
|---|---|---|
| Product scope | One manually bounded isolated sign per capture; no automatic segmentation or continuous translation | `src/isl_recognition/webapp.py`, `src/isl_recognition/static/app.js`, `README.md` |
| Model family | AI4Bharat INCLUDE Transformer-small adapter | `src/isl_recognition/model.py` |
| Class vocabulary | Full 50-class INCLUDE-50 label map and stable indices | `config/label_map_include50.json`, `src/isl_recognition/labels.py` |
| Per-frame feature shape | 134 float32 values: 25 pose XY + 21 hand-1 XY + 21 hand-2 XY | `src/isl_recognition/feature_extractor.py`, `src/isl_recognition/landmarks.py` |
| Preprocessing | Linear interpolation of missing coordinates, zero fill, pad/truncate to 169 frames | `src/isl_recognition/preprocess.py` |
| Model input | `(1, 169, 134)` | `src/isl_recognition/model.py`, tests for preprocessing/model contract |
| Browser image path | Canvas width 640 px, proportional height, JPEG quality 0.92, 40 ms timer target, duplicate `video.currentTime` suppression | `src/isl_recognition/static/app.js` |
| Text threshold | 0.40; explicitly provisional and uncalibrated | `src/isl_recognition/app_config.py` |
| IoT threshold | Separate 0.70 provisional gate; not a text threshold | `src/isl_recognition/app_config.py` |
| IoT state | `pre_iot_lockdown=True` in code; map empty by default | `src/isl_recognition/app_config.py`, `config/iot_label_map.json`, `src/isl_recognition/webapp.py` |

### Detector provenance limitation

The active adapter obtains modern MediaPipe full-Pose landmarks and slices the first 25 positions for the expected feature slots. This is shape/slot-compatible; it is **not evidence of coordinate-level parity** with the historical `upper_body_only=True` detector. The original historical extractor version/build metadata and an exact runnable reference generator are not present in the source archive. Do not silently substitute a MediaPipe wheel or reinterpret this as proven parity.

## 3. Handover experiment and decision reconciliation

The handover's completed 20-seed dropout experiment is accepted as historical evidence and is not repeated. The only upstream source correction expected by the handover is the evaluation-aware dropout call:

```python
F.dropout(x, p=0.2, training=self.training)
```

The checkpoint must remain byte-identical. The Phase 1 evaluator now verifies the pinned repository commit, the expected checkpoint SHA-256 and the exact single-line upstream worktree correction before it will load the model. It does not rerun the completed seed experiment.

The Q72 image-quality sweep is historical. The current production path is 640 px Canvas JPEG quality 0.92. Legacy Q72 diagnostic scripts are labelled historical; they are not the RC3 production-equivalent benchmark. The earlier eight-clip browser transport pilot is also retained as pilot evidence only; it is not the 77-video official validation benchmark.

Historical metrics in the handover (including Top-1 64.94%, Top-5 89.61%, macro-F1 0.623 and weighted-F1 0.606) are not represented as re-run results in this audit. Their transform/environment caveats remain applicable.

## 4. Source archive inventory and omissions

### Present in the uploaded source archive

- FastAPI/JavaScript application source and static assets.
- 26 test modules before the audit additions, with backend, preprocessing, labels, IoT protocol, database and webapp tests.
- Windows setup/configuration/run scripts and Python diagnostics.
- `config/`, `data/`, `docs/`, `esp32/`, `broker/`, `experiments/` and notebook content.
- Implementation plan and worklog.
- A metadata-only INCLUDE video index (`data/include50_video_metadata.json`).

### Absent from the uploaded source archive

1. **AI4Bharat INCLUDE Git checkout and `.git` history**, required at pinned commit `df348f4a640060d4d39f02556d9f96851e8c46c7`. The source ZIP itself has no `.git`, so its own branch/HEAD/working-tree state cannot be asserted.
2. **Locked model checkpoint** `include50_no_cnn_transformer_small.pth`. The required SHA-256 is `8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4`; because the checkpoint is absent, this digest is not verified here.
3. **The 77 original official validation video files.** The metadata index is not video content. The browser capture workflow also requires the local validation files.
4. **The target Windows Python 3.12 `.venv` and its pinned dependencies.** This environment cannot validate the user's target machine.
5. **Original historical extractor generator provenance** (exact MediaPipe version, source script and generation metadata) needed to claim historical detector parity.
6. **A labelled user-webcam capture set** for the later user-capture phase; no such capture set was included.

The runtime review container also lacks `mediapipe`, `transformers` and `paho-mqtt`; the actual model cannot be loaded here.

## 5. Defects found and remediation in this working release

| Finding in uploaded source | Impact | Remediation in this package |
|---|---|---|
| `.env.example` missing although `setup_windows.ps1`, `start_app.ps1` and `configure_local_assets.ps1` copy it | Fresh install fails before app setup | Added safe `.env.example` with loopback binding, `.40` text threshold, `.30` hand ratio, IoT false, empty credentials and no secrets |
| `requirements-dev.txt` declared `httpx2`, while Starlette TestClient imports `httpx` | Test-client dependency mismatch | Replaced with `httpx>=0.27,<1` |
| Release metadata had mismatched app/project versions; stale egg-info was included | Misleading package identity and metadata | Aligned FastAPI/pyproject/release identity to `1.0.0-rc3`; removed stale generated egg-info from the clean package |
| Initial `SHA256SUMS.txt` had five incorrect digests | Release integrity check unreliable | Regenerated checksums for the clean final package after all edits |
| Several docs described stale test counts and Q72 as active | Incorrect operations guidance | Updated current status/report pages; retained Q72 documents only as explicitly historical evidence |
| `diagnose_reference_video.py` defaulted to JPEG quality 72 | New run could unintentionally reproduce obsolete transform | Default changed to 92; historical comparator scripts remain visibly labelled |
| Legacy browser transport batch defaulted to an eight-clip pilot | Easy to misrepresent as complete validation | Marked as pilot-only and added distinct browser-native 77-clip capture/evaluation tools |
| `.env.backup.20261106-141651` carried stale hand-ratio `0.50` while code default is `0.30` | Could lead to accidental config drift | Backup excluded from the clean release; observed sanitized values are documented here only |
| Archive included old patch/backup directories and accidental brace-named paths | Ambiguous active source and noisy package | Excluded patch bundles, caches, backups and malformed empty paths from clean release |

The original root-level `APPLY_ISL_METADATA_HOTFIX.ps1` referred to an excluded historic hotfix folder and was not a valid current installer; it, `README_HOTFIX.txt` and superseded `PATCH_NOTES.txt` were omitted from the clean release. The initial uploaded `SHA256SUMS.txt` mismatched five files: `.gitignore`, `scripts/diagnose_reference_parity.py`, `scripts/download_include_reference_clips.py`, `scripts/evaluate_dataset.py`, and `tests/test_selective_reference_downloader.py`. The final manifest is regenerated against the final clean package.

## 6. Test and static-check evidence

Executed from the clean working copy on 7 November 2026:

```text
PYTHONPATH=src:scripts:. python -m pytest -q --disable-warnings
160 passed, 1 skipped in 5.66s
```

The skipped test is the real model-contract test, which requires the external AI4Bharat checkout and checkpoint. Environment: Linux review container, Python 3.13.5, pytest 9.0.2, Torch 2.10.0+cpu, OpenCV 4.13.0.92, NumPy 2.3.5; `mediapipe`, `transformers` and Paho MQTT are absent. Project requirements declare pytest 8.x and user target is Windows/Python 3.12, so this is not target-environment verification.

Also executed successfully:

- `python -m compileall -q src scripts tests`.
- `node --check` for `app.js`, `landmark_diagnostics.js` and `landmark_worker.js`.
- `--help` smoke checks for the new browser-capture server, evaluator and official validation manifest generator.
- `scripts/preflight.py`, which correctly reports missing MediaPipe, Transformers, Paho MQTT, AI4Bharat repo and checkpoint, and confirms the hard IoT lock and zero mappings.

These checks establish software regression coverage and Python/JS syntax only. They do **not** establish checkpoint loading, MediaPipe runtime behaviour, model accuracy, browser capture quality on the user's device, Windows 3.12 compatibility, webcam latency or hardware behavior.

## 7. Phase 1 work implemented (not yet executed)

The package now includes:

- `scripts/browser_capture_protocol.py` — versioned binary payload, contract/identity/timestamp/dimension/JPEG and safety validation.
- `scripts/serve_browser_validation_capture.py` — loopback-only receiver that verifies the exact pinned official 77-row manifest, writes the exact browser JPEG bytes and capture metadata, supports verified resume and explicit overwrite, and exposes no application/MQTT/IoT route.
- `scripts/browser_validation_capture.html` — browser-native video playback capture using the deployed 640 px / JPEG 0.92 Canvas transform, 40 ms sampling timer, duplicate-frame suppression and 6 s / 169-frame cap. It records browser decoder frame counters and keeps classifier input unmirrored.
- `scripts/evaluate_browser_canvas_validation.py` — paired browser Canvas vs OpenCV-native timestamp-matched path diagnostics. It reports capture/detector counts, pose/hand coverage, coordinate-level differences for pose/hand slots, model-input differences, logits/probability and top-1/top-5 differences, recognition metrics, class metrics, confusion matrices and risk/coverage. It verifies the source commit, exact dropout correction, checkpoint digest, official 77-video split, text threshold and IoT-off state before inference.

The OpenCV-native path is an explicitly labelled reference, not a claim that OpenCV and Chrome decode source video identically. The local browser capture removes WebSocket backpressure to isolate Canvas/extractor behavior; this does not replace a separate production webcam/WebSocket transport profile.

**Phase 1 has not been run.** It is blocked by the absent AI4Bharat checkout, checkpoint, 77 source videos, MediaPipe dependency and target Windows environment. No new recognition metric or accuracy result is claimed.

## 8. Phase 0 exit and next milestone

**Phase 0 exit:** achieved for the two supplied ZIPs. The active implementation has been reconciled against the handover; missing assets, stale material and known source defects are recorded; safe software defects have been corrected in an isolated clean working copy.

**Next milestone:** Phase 1 — capture and run the paired 77-clip browser Canvas validation after the external assets are present. Follow `PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md`. Keep the user webcam collection (Phase 3), historical extractor provenance (Phase 2) and IoT/hardware (later phases) separate. No recognition or IoT release gate can be marked GO from the current evidence.

---

## Superseding execution update — 7 November 2026

The “Phase 1 not run” statement in this original Phase 0 audit is historical and superseded. On the target Windows 11 / Python 3.12.10 machine, the user verified the checkpoint SHA-256 and strict checkpoint loading (1 passed), then ran the full pre-Phase-2 regression suite (161 passed, 0 failed). The exact official validation split was resolved locally at 77/77, captured in the browser at 77/77, and evaluated through the paired browser/OpenCV pipeline. Browser Canvas top-1 was 44/77; OpenCV native top-1 was 46/77. See `engineering/PHASE2_ROOT_CAUSE_ANALYSIS.md` and `engineering/phase1_evidence/` for full analysis and artifacts. Phase 2 four-path all-77 ablation is the next pending target-Windows gate. This update does not change the unresolved historical MediaPipe detector provenance limitation.
