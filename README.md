# ISL Recognition Project — Manual INCLUDE-50 Recognition MVP

**Version:** 1.0.0-rc3 (implementation package, 6 November 2026)
**Scope:** manual Start/Stop capture of one isolated INCLUDE-50 sign per inference. This is not continuous sign-language translation.

This package consolidates the recognition web app, MediaPipe diagnostics, INCLUDE-50 reference gallery, manual capture UX, evaluation tooling, and fail-closed pre-IoT safety lock. It retains the 50-class classifier and 134-D/169-frame input contract. Only two owner-selected labels may later be routed to IoT; the default map is empty.

## Current implementation and handoff plan

Read [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the phased implementation and acceptance gates, [`IMPLEMENTATION_WORKLOG.md`](IMPLEMENTATION_WORKLOG.md) for changes actually made, and [`docs/ENGINEERING_DECISIONS.md`](docs/ENGINEERING_DECISIONS.md) for scope decisions. The default `config/iot_label_map.json` intentionally contains no mappings, and application-level pre-IoT lockdown remains enabled.

The primary webapp uses manual Start/Stop, buffers JPEG frames for one capture, then runs sequential MediaPipe extraction and one model inference after Stop. It does not currently use an asynchronous bounded frame queue.

## Important release status

This is a **Pre-IoT Release Candidate**, not yet certified as production-ready for arbitrary real-world signers. It includes tests that can be run from this archive, and it incorporates your benchmark evidence. It cannot verify your physical Windows webcam in this environment. Recognition accuracy and threshold calibration still require an independent, labeled webcam evaluation set.

## Required external assets

The model checkpoint and INCLUDE videos are intentionally not bundled. On the target Windows machine, the current known paths are expected to be:

- `C:\AIAssets\INCLUDE` — AI4Bharat INCLUDE source checkout
- `C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth` — checkpoint
- `C:\AIAssets\INCLUDE_videos` — optional local reference gallery videos
- `data\include50_video_metadata.json` — optional metadata index

The release will not invent labels or pretend a missing model is ready. The model/API health endpoint will report missing or invalid external assets.

## Replacing the accumulated patch-based project

Stop Uvicorn and extract this package into a **separate new folder** first. Do not extract over the active project. From the extracted package, run `scripts\phase0_backup_and_git.ps1 -ProjectRoot <ACTIVE_PROJECT_PATH>` with the path to your actual current project (not the extracted package directory). Review the backup and Git status, then merge/copy the implementation into the active project. Review `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_WORKLOG.md` before applying changes. Keep the AI4Bharat checkout, checkpoint and video archives under `C:\AIAssets` unchanged.

If the old project contains `data\include50_video_metadata.json`, preserve it outside the old folder before renaming, for example:

```powershell
Copy-Item .\ISL_Recognition_Project\data\include50_video_metadata.json C:\AIAssets\include50_video_metadata.json
```

After extracting the new folder, pass that path to `configure_local_assets.ps1`. The metadata file is a small index; raw reference videos remain in `C:\AIAssets\INCLUDE_videos`.

## Setup on Windows

Use 64-bit Python 3.12. Open PowerShell in this project directory. If your environment blocks unsigned scripts, use a process-scoped policy only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\setup_windows.ps1
```

If the external assets already exist at the paths above, configure them and enforce the pre-IoT settings:

```powershell
.\scripts\configure_local_assets.ps1
```

The script backs up `.env` before modifying it. Review the reported missing paths; it does not download the large checkpoint or video dataset. Restart the application after any `.env` change.

Apply or verify the deterministic inference correction in the external AI4Bharat checkout (idempotent; makes a backup and does not change checkpoint weights):

```powershell
.\scripts\fix_include_inference_dropout.ps1
```

If the script says it is already fixed, do not edit the upstream file manually.

## Run the app

```powershell
.\scripts\start_app.ps1
```

Open `http://127.0.0.1:8000`. Check `http://127.0.0.1:8000/api/health`. A usable recognition runtime requires `model_ready=true`, `label_map_ready=true`, and `extractor_ready=true`. Use the reference gallery to select a class and explicitly set it as the expected sign before comparing a practice attempt.

The app uses a 2-second preparation countdown and up to 6 seconds of recorded video. The recording maximum is a user-experience setting, not a requirement to capture 169 frames. Every captured frame is extracted after recording; the classifier input is padded to 169 rows × 134 features.

## Pre-IoT lock

The pre-IoT lock is hard-coded `True` for this release and enforced by both the UI and backend; it cannot be turned off via `.env`. The backend rejects enable attempts and suppresses device commands even if a process-local flag is accidentally set. Keep `ISL_IOT_ENABLED=false`. A later IoT release requires a separate code review, modified safety gates, and its own hardware tests.

## Confidence policy

The current web text-recognition threshold is `0.40`, explicitly marked **uncalibrated**. The independent IoT command threshold defaults to `0.70` and is also provisional. A predicted label and top-five candidate list are shown separately from accepted/rejected status; a score does not prove correctness. Evaluate the exact 77-item official validation split and report risk/coverage before selecting any threshold.

## Consolidated stabilization campaign

Read `engineering/PHASE0_SOURCE_AUDIT.md` for the source/handover reconciliation and missing-assets register. `engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md` explains the browser-native 77-clip validation workflow. The new `scripts/run_phase1_browser_capture.ps1` starts the loopback-only Canvas capture receiver; it does not start the application, MQTT or IoT.

The consolidated Windows runner is `RUN_COMPLETE_ISL_STABILIZATION.ps1`. It verifies checksums, Python compilation, JavaScript syntax, the regression suite, preflight and IoT-off configuration. To run the Phase 1 evaluator as part of the runner, first complete the 77 browser captures and then add `-RunPhase1`. Phase 1 requires the pinned AI4Bharat Git checkout, the exact checkpoint file and all 77 official validation videos.

## Run release checks

```powershell
.\scripts\run_pre_iot_release_check.ps1
```

This runs Python compilation, JavaScript syntax checks if Node.js is available, tests, dependency preflight, and a configuration check for the pre-IoT lock. The consolidated campaign runner is `RUN_COMPLETE_ISL_STABILIZATION.ps1`. The offline test suite does not replace a Windows webcam test, external model load test, or independent recognition evaluation.

## Reproducible benchmark commands

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_reference_gallery.py --video-root C:\AIAssets\INCLUDE_videos --resize-width 0 --samples-per-class 1 --output .\outputs\reference_gallery_native.json
.\.venv\Scripts\python.exe .\scripts\benchmark_reference_gallery.py --video-root C:\AIAssets\INCLUDE_videos --resize-width 640 --samples-per-class 1 --output .\outputs\reference_gallery_640.json
```

The recorded single-clip-per-class benchmark outputs live under `experiments/`. They are diagnostic smoke benchmarks, not an official test set and may not be independent of training data.

## Document map

- `PROJECT_CONTEXT.md`, `PROJECT_SPEC.md` — project scope and model contract
- `docs/PRODUCTION_READINESS_CONTRACT.md` — gates that must pass before IoT
- `docs/LIVE_LANDMARK_DIAGNOSTICS.md` — preview behavior and limitations
- `docs/REFERENCE_GALLERY_BENCHMARK.md` — benchmark methodology
- `docs/REFERENCE_PIPELINE_PARITY_COMPAT_FIX.md` — legacy MediaPipe limitation
- `docs/DETERMINISTIC_INFERENCE_FIX.md` — upstream dropout correction and rollback
- `docs/RUNBOOK.md`, `docs/EVALUATION.md` — operations and evaluation
- `engineering/PHASE0_SOURCE_AUDIT.md` — full uploaded-archive audit and reconciliation
- `engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md` — reproducible 77-clip browser Canvas benchmark

## Known, intentionally unresolved release gates

1. Exact reproduction of the historical INCLUDE upper-body extractor is not proven because MediaPipe 0.10.21 no longer accepts the original `upper_body_only=True` option; the parity adapter is shape-compatible, not historically exact.
2. The 29-video benchmark is small and not guaranteed to be independent of training data.
3. Webcam recognition, preview responsiveness and latency must be tested on the actual Windows host.
4. Final confidence threshold calibration requires independent labeled recordings.
5. Physical IoT behavior is deliberately outside this release and locked off.

Do not represent this release as a general-purpose ISL translator or as production-certified until these limitations are resolved and the acceptance criteria in the contract are met.


## Engineering continuation status — 7 November 2026

Phase 1 was executed on the target Windows environment: the 77-item official INCLUDE-50 validation split was captured through browser Canvas and evaluated against the locked Transformer checkpoint. The browser path recorded 44/77 Top-1 correct (57.14%); the OpenCV native reference recorded 46/77 (59.74%). These are low-support validation results (77 examples across 50 classes), not a release-quality claim.

Phase 2 root-cause analysis and the resumable four-path all-77 feature-provenance runner are included. Run `engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md` from the project root to compare native application extraction, resized PNG, resized JPEG q92, and the pinned upstream generator through the modern MediaPipe compatibility adapter. This q92 OpenCV branch approximates (but is not identical to) browser Canvas. The historical MediaPipe build used to create training keypoints remains unverified.

The model, checkpoint, INCLUDE-50 ordering, 134-D feature layout, 169-frame model input, and provisional thresholds remain unchanged. IoT remains disabled. The recognition-demo release remains NO-GO pending target-Windows Phase 2 execution and live-webcam validation.
