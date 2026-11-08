# ISL Recognition Product — Implementation Plan and Delivery Status

**Project:** Deep Learning, Computer Vision and IoT-Based Physical Response for Indian Sign Language Recognition  
**Implementation baseline:** 6 November 2026 source snapshot, amended by this engineering work package  
**Primary target:** Windows 11, Python 3.12, FastAPI + browser JavaScript, AI4Bharat INCLUDE-50  
**Release posture:** software MVP; IoT and hardware remain disabled until explicit release gates pass

## 1. Product scope locked

The MVP recognises **one isolated ISL sign per capture**. The signer starts and stops the capture manually. The application then runs exactly one recognition inference and displays the result as text.

The recogniser retains the full 50-class AI4Bharat INCLUDE-50 vocabulary and original class order. The existing 134-dimensional landmark representation and 169-frame input contract remain unchanged. A separate backend IoT router may later map **exactly two signs selected by the project owner** to safe LED/buzzer commands. No sign is preselected in this package; the shipped IoT mapping is empty.

The MVP does not perform automatic sign-boundary detection, continuous multi-sign translation, sentence generation, or inference on the ESP32. Those are future research extensions, not unfinished requirements of this release.

## 2. System architecture

```text
Browser camera
  └─ User Start → one sign → User Stop
       └─ timestamped JPEG frames over WebSocket
            └─ FastAPI capture validation
                 └─ MediaPipe landmark extraction
                      └─ existing 134-D X/Y feature sequence
                           └─ established interpolation + 169-frame pad/truncate
                                └─ pretrained INCLUDE-50 Transformer-small
                                     └─ class + score + candidate list
                                          ├─ Text UI + SQLite recognition history
                                          └─ IoT command router (OFF by default)
                                               ├─ accepted result only
                                               ├─ exact two-label allowlist
                                               ├─ independent IoT confidence gate
                                               ├─ one command per capture + cooldown
                                               └─ MQTT command/ack → simulator → (later) ESP32
```

Text recognition and IoT actuation are deliberately separate. A recognised class can be displayed even when it is not mapped to a device command. No IoT mapping changes the classifier's 50 output classes.

## 3. Engineering principles

1. Preserve the model/checkpoint contract: 50 classes, official index ordering, 134 features, 169 timesteps, float32 finite inputs.
2. Keep test data locked until the model, preprocessing, capture settings and decision thresholds are frozen.
3. Use the exact pinned upstream `include50_val.txt` file to define the 77 validation examples. Broader metadata matches are not a substitute for the official split list.
4. Distinguish software tests, reference-video evaluation, live webcam performance, external-signer testing, MQTT simulation and physical actuation. Passing one does not prove another.
5. Do not change MediaPipe versions, feature ordering, hand ordering, temporal resampling, checkpoint or confidence threshold without a paired experiment and retained results.
6. Fail closed: missing/invalid IoT configuration, an unmapped class, a rejected prediction, a low-confidence score, duplicate capture or MQTT failure must never trigger an action.
7. Do not claim model accuracy, real-world robustness or hardware success until the matching evidence exists.

## 4. Implementation phases and acceptance gates

### Phase 0 — Protect the user's working environment

**Actions**
- Make a timestamped source backup before applying a patch.
- Initialise local Git only if the project is not already a Git repository.
- Add ignore rules for `.venv`, `.env`, checkpoints, downloaded videos, caches, logs and local database files.
- Preserve the existing Python 3.12 environment; do not delete/rebuild it as a first troubleshooting step.

**Gate**
- A backup folder exists outside the project root.
- Existing Git repositories/remotes are left untouched.
- Asset and credential files are not staged into Git.

**Delivered in this package:** `scripts/phase0_backup_and_git.ps1` (local Windows execution required).

### Phase 1 — Reproduce the baseline runtime and assets

**Actions**
- Confirm Python 3.12 and run the project preflight.
- Verify the local AI4Bharat checkout, the pinned commit, checkpoint SHA256, official label map, and required dependencies.
- Run all unit/API tests and a strict checkpoint-load smoke test.
- Keep CPU fallback available; only enable CUDA after `torch.cuda.is_available()` and a real model smoke test succeed.

**Gate**
- The exact checkpoint loads strictly into the intended model.
- Model input is `(1,169,134)` and output is `(1,50)`.
- The output index-to-label map is identical to the bundled official mapping.
- A deterministic repeated-inference check is recorded.

### Phase 2 — Establish the complete official validation set

**Actions**
- Verify AI4Bharat `HEAD` equals `df348f4a640060d4d39f02556d9f96851e8c46c7`.
- Verify `train_test_paths/include50_val.txt` SHA256 equals `044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541`.
- Parse exactly 77 unique paths, mapped to all 50 official labels.
- Download only missing validation members with the existing HTTP Range downloader; do not fetch test clips.
- Write `outputs/include50_official_val_manifest.csv`.

**Gate**
- The manifest contains exactly 77 unique validation rows.
- Local availability is reported separately. All 77 videos must be available before a complete validation metric is claimed.

**Delivered in this package:** `scripts/prepare_official_validation_manifest.py` and `validation-all` profile in `scripts/download_include_reference_clips.py`.

### Phase 3 — Full validation evaluation and threshold analysis

**Actions**
- Run the official validation manifest with the exact baseline checkpoint and approved preprocessing.
- Record top-1 and top-5 accuracy, macro precision/recall/F1, weighted F1, per-class report, confusion matrix, capture/extraction/inference latency and failed/missing sample counts.
- Produce a descriptive confidence risk/coverage table at `.40`, `.50`, `.60`, `.70`, `.80`, `.90`.
- Review per-class failure examples and confidence distributions. Do not select a threshold by maximizing a tiny pilot subset.
- Keep the classifier threshold explicitly marked uncalibrated until validated and reviewed.

**Gate**
- 77/77 official validation videos are present and successfully evaluated for a complete official-split result.
- Any partial run is marked incomplete; no percentage from it is described as full-split accuracy.
- Threshold choice is documented with accepted coverage, selective accuracy/error, and false-accept implications.

**Delivered in this package:** `scripts/evaluate_dataset.py` defaults to `val`, reports top-5/risk-coverage/latency, and prevents accidental test evaluation.

### Phase 4 — Capture-path validation

**Actions**
- Keep the manual Start/Stop workflow. The user performs one sign per capture.
- Measure browser frame cadence, received frames, JPEG bytes, dropped frames, MediaPipe extraction time, preview overhead and Stop-to-result latency.
- The current browser capture is provisionally set to 640px JPEG quality `.92`, based on an earlier five-clip screening in which Q92 preserved more hand coverage than Q72 for one difficult clip. This is a capture-fidelity setting, not an accuracy claim.
- Keep classifier input unmirrored. The visual preview may be mirrored for natural interaction; do not mirror model input without paired validation.
- Profile whether the separate landmark preview session competes with the classifier extraction session. Disable or pause the extra preview during recording if measurements show interference.
- Investigate the measured slow section first. Historical diagnostics indicate MediaPipe extraction dominates Transformer inference; do not optimise the Transformer first.

**Gate**
- Camera path produces valid timestamped frames and a clearly bounded single capture.
- Empty, short, corrupted, low-quality and cancelled captures are safely handled.
- Stop-to-result latency and extraction p50/p95 are reported on the actual Windows target.

### Phase 5 — Validate preprocessing parity before model changes

**Actions**
- Compare the same source frames through the current app extractor and the pinned upstream extractor where an installable historical environment is available.
- Compare each of the 134 coordinates, missing-value patterns, feature coverage, padded sequence, logits and predicted classes.
- Investigate the known detector difference: upstream historically used `upper_body_only=True`, removed in MediaPipe 0.8.4; current code uses modern full-pose outputs and the first 25 pose slots. Index compatibility does not prove coordinate parity.
- Preserve the present hand-slot policy unless evidence demonstrates a systematic error.

**Gate**
- Any new extractor path is compared on the full validation set, not selected examples alone.
- A feature-contract change is versioned as a separate model pipeline and cannot silently consume the existing checkpoint.

### Phase 6 — Model improvement only if the baseline evidence justifies it

**Actions**
- First classify errors into capture-quality, detector/preprocessing, class confusion, signer variation and out-of-vocabulary rejection.
- Preserve the current pretrained checkpoint as the baseline candidate.
- If warranted, create a separate experiment branch for fine-tuning or a landmark-sequence model; train/validate on the correct split and never use test results for model selection.
- Holistic/GCN/LSTM/Transformer alternatives must be treated as separate input/model contracts. A 543-landmark Holistic feature vector is not a drop-in replacement for the current 134-D checkpoint.

**Gate**
- Candidate model is evaluated under the same official split and preprocessing policy.
- The selected candidate improves the target metric without hiding per-class regressions, capture failures or external-signer failures.

### Phase 7 — IoT routing and simulator release

**Actions**
- The project owner chooses exactly two INCLUDE-50 labels for the hardware demo.
- Configure `config/iot_label_map.json` with exactly two label-to-command entries. Until then it remains empty and IoT cannot be enabled.
- Keep the 50-way classifier unchanged. The backend router checks recognition acceptance, exact allowlist membership, the independent IoT minimum confidence (`0.70` provisional), one-action-per-capture, cooldown, and command TTL.
- Run local Mosquitto + simulated ESP32 only. Check command IDs, QoS 1 publish, acknowledgement correlation, duplicate rejection, expiration, invalid schema, low confidence, no mapping and broker failures.
- Keep hardware code/firmware locked until simulator and software-ready gates pass.

**Gate**
- Every negative scenario results in zero command publication/actuation.
- Each of the two selected labels produces only its approved command in positive tests.
- Duplicate message IDs are idempotently rejected by the simulated endpoint and firmware review.
- MQTT credentials are outside source control; broker/topic/firewall are restricted.

**Delivered in this package:** empty-by-default JSON map, mapping loader, independent IoT confidence gate, health reporting, strict enable guard, and simulator configuration validation.

### Phase 8 — External signer and physical-device validation

**Actions**
- Evaluate signs demonstrated by signers not represented in the model's training/reference examples where practical. Record signer, session, camera, lighting, distance, orientation and sign variant.
- Include repeated captures, one-hand/two-hand signs, partial occlusion, no-hand frames, idle movements and non-INCLUDE gestures.
- Verify open-set rejection behaviour. Softmax always ranks one of the 50 known classes; a high score is not proof that an unknown gesture belongs to the vocabulary.
- Only after software readiness, connect an ESP32 and a low-voltage LED/buzzer circuit with current-limiting components; verify the wiring and power separately from application tests.

**Gate**
- The app consistently reports accepted/rejected states and never actuates on unknown, ambiguous, invalid or non-allowlisted gestures.
- External signer results and all hardware tests are recorded. A simulated LED blink is not described as physical actuation.

### Phase 9 — Release packaging and evidence

**Actions**
- Keep source, tests, configuration, and documentation in the release bundle.
- Exclude `.venv`, secrets, model checkpoints, video archives, databases, caches and generated runtime outputs.
- Publish a release manifest with exact asset hashes and environment details.
- Re-run full tests, Python compilation and JavaScript syntax check.

**Gate**
- A clean install from the release bundle works on the target Windows environment.
- All known limitations and unavailable validation evidence are disclosed.

## 5. Current implementation status

| Work item | This package | Still requires local execution/evidence |
|---|---|---|
| Manual isolated-sign workflow | Existing workflow retained; no auto segmentation added | Webcam end-to-end check on Windows |
| INCLUDE-50 class contract | Existing 50 labels/134-D/169 frames retained | Strict checkpoint smoke test in user's Python 3.12 env |
| JPEG capture | Provisional quality changed to 0.92 at 640px; raw orientation retained | Paired webcam/validation run and browser capture benchmark |
| IoT mapping | Removed global legacy mapping; JSON is empty by default; exactly two required | Owner selects two labels; then simulator/firmware alignment |
| IoT gate | Separate 0.70 provisional command threshold and fail-closed route | Full validation and hardware safety review before unlocking |
| Official val manifest | Pinned split parser added; 77-item guard | Run against local Git checkout and download any missing videos |
| Evaluation runner | Val default, test lock, top-1/top-5, per-class, risk/coverage, latency | Run against the actual checkpoint and all 77 local videos |
| Software regression | 160 passed, 1 skipped in the 7 Nov 2026 Linux review container | Re-run on user's target Windows `.venv` |
| Physical ESP32 | Not enabled | Hardware wiring, firmware review, broker and device acknowledgement tests |

## 6. Known blockers and evidence boundaries

This container does not include the user's active Windows `.venv`, real MediaPipe runtime, official checkpoint binary, local video bytes, webcam, Mosquitto broker, or ESP32. Therefore this implementation package does **not** claim full validation accuracy, target-laptop latency, real webcam recognition correctness, broker interoperability or physical device operation. Those are intentionally staged as local execution gates.

## 7. Local execution order

From PowerShell in the **extracted implementation-package directory**. First set `$ActiveProject` to the folder that currently contains your working project (not the new extracted folder), then back it up:

```powershell
# Replace this path with the actual current project folder on your laptop
$ActiveProject = 'C:\path\to\your\current\ISL_project'
.\scripts\phase0_backup_and_git.ps1 -ProjectRoot $ActiveProject
# Review the backup path and `git status --short` before copying any implementation files.

# After you have merged the package into the active project, run the following commands there:
Set-Location $ActiveProject

# 1. Activate the existing Python 3.12 environment; do not rebuild it yet
.\.venv\Scripts\Activate.ps1
python --version
python .\scripts\preflight.py
python -m pytest -q

# 2. Build the exact 77-item official validation manifest (requires pinned repo clone)
python .\scripts\prepare_official_validation_manifest.py --repo C:\AIAssets\INCLUDE

# 3. Preview and download only missing official validation videos; type FETCH CLIPS at the prompt
python .\scripts\download_include_reference_clips.py --profile validation-all --ai4bharat-repo C:\AIAssets\INCLUDE --destination C:\AIAssets\INCLUDE_videos --download

# 4. Evaluate the complete official validation set (do not use --split test)
python .\scripts\evaluate_dataset.py --manifest .\outputs\include50_official_val_manifest.csv --data-root C:\AIAssets\INCLUDE_videos --split val --out-dir .\outputs\include50_validation_baseline
```

The `evaluate_dataset.py` output must report `evaluation_complete_for_official_split: true` before it is called a complete 77-video validation result. Test evaluation remains locked until the model, pipeline and thresholds are frozen.


## 7 November 2026 stabilization addendum

The Phase 0 uploaded-source audit is complete; see `engineering/PHASE0_SOURCE_AUDIT.md`. Browser-native Phase 1 capture/evaluation scripts have been added and are described in `engineering/PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md`. Their execution remains gated on the pinned upstream Git checkout, exact locked checkpoint, all 77 original validation videos, MediaPipe runtime and target Windows Python 3.12. Current review-container regression evidence is 160 passed / 1 skipped and does not substitute for these missing model/browser validations.

---

## Execution update — 7 November 2026 (supersedes earlier pending statements)

Phase 1 is no longer pending. The target Windows run completed the 77/77 official validation browser capture and paired browser/OpenCV inference. Results: browser Canvas top-1 44/77 (57.14%), top-5 67/77, macro F1 0.5207; OpenCV native top-1 46/77 (59.74%), top-5 69/77, macro F1 0.5594. The checkpoint strict-load test passed on Windows and the full pre-Phase-2 Windows test suite was 161 passed, 0 failed. Detailed results and retained artifacts are in `engineering/phase1_evidence/` and `engineering/PHASE2_ROOT_CAUSE_ANALYSIS.md`.

Phase 2 four-path provenance/resize/JPEG/upstream-modern-adapter tooling has been implemented and tested in the review copy. The user's all-77 target-Windows Phase 2 run is the current next gate. Do not treat Phase 2 as executed until `outputs/phase2_feature_provenance_77/summary.json` reports `status=completed`, `successful_count=77`, `failed_count=0`. The historical detector parity limitation remains unresolved. The test split, model, checkpoint, class order, feature dimensions, sequence length, thresholds and IoT settings remain locked.
