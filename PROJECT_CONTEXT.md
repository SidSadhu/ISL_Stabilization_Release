# Current execution status — 7 November 2026 (supersedes earlier status snapshots)

**Phase 1 is complete on the user's target Windows 11 / Python 3.12.10 environment.** The official INCLUDE-50 validation split contains 77 clips across all 50 labels; its pinned path-list SHA-256 is `044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541`. All 77 source videos resolved locally and all 77 browser-native Canvas diagnostic captures completed. The browser capture run resumed from clip 15 without recapturing the first 14. The previously failing `quiet/MVI_9537.MOV` clip was saved with 67 frames. The receiver reported `77/77 received`.

The target Windows checkpoint-dependent strict-load test passed (1 passed in 7.35 s), followed by the full pre-Phase-2 Windows regression suite (161 passed, 0 failed, 1 non-fatal Starlette/httpx deprecation warning). XGBoost was pinned as `xgboost==3.4.1` because the pinned upstream package import chain imports it unconditionally.

Phase 1 paired evaluation completed on all 77 official validation clips. Browser Canvas: top-1 44/77 (57.14%), top-5 67/77 (87.01%), macro F1 0.5207. OpenCV native reference: top-1 46/77 (59.74%), top-5 69/77 (89.61%), macro F1 0.5594. Both paths made the same top-1 prediction on 66/77 clips. These are descriptive validation results, not a release-readiness claim. Text threshold 0.40 remains uncalibrated; test split remains unused.

Phase 2 source audit, reproducible tooling, report generation and regression tests are complete in this candidate. The all-77 four-path feature provenance/transport ablation has **not yet been run on the user's Windows machine**. That is the next execution gate. The package-review environment test suite is **168 passed, 1 skipped** (Python 3.13.5); the skip is the external-checkpoint test, which has already passed on the user's Windows environment before Phase 2 tooling was added. Re-run the full suite after overlaying this candidate.

**Locked:** original Transformer-small checkpoint and weights, 50-class ordering, 134-D feature layout, 169-frame input, x×1920/y×1080 coordinate scaling and production confidence setting. **IoT remains disabled**, mapping is empty, and code-level pre-IoT lockdown remains true. No training, threshold change, test-split evaluation, MQTT, broker, simulator or ESP32 action is part of Phase 2.

See `engineering/PHASE2_ROOT_CAUSE_ANALYSIS.md`, `engineering/PHASE2_HISTORICAL_PROVENANCE.md`, `engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md` and `engineering/PHASE2_DECISION_AND_GO_NO_GO.md`.

---

# Authoritative Project Context

**Status date:** 6 November 2026  
**Authority:** This file is the single source of truth for current project decisions.  
**Project state:** The manual isolated-sign capture flow and INCLUDE-50 software baseline are retained. This implementation package adds empty-by-default two-label IoT routing, official 77-item validation-manifest tooling, and explicit evaluation safeguards. In the isolated work copy, 119 tests passed and one checkpoint-dependent test was skipped. The complete validation set, official checkpoint inference, Windows webcam, live broker, and ESP32 hardware remain unverified. See `IMPLEMENTATION_PLAN.md`, `IMPLEMENTATION_WORKLOG.md`, and `RELEASE_MANIFEST.json` for the current evidence boundary.

> When this file conflicts with older recommendations in `README.md`, `PROJECT_SPEC.md`, `docs/HANDOFF.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/RUNBOOK.md`, or `STRUCTURE_STATUS.md`, follow this file. Historical experiment records in `docs/EXPERIMENT_LOG.md` must remain historical and must not be rewritten to match later decisions.

## 1. Official title and subtitle

### Official project title

**Develop a Real-Time Deep Learning-Based System for Recognising Isolated Sign Language Gestures and Converting Them into Text**

### Descriptive subtitle

*Computer Vision, Deep Learning and IoT-Based Physical Response for Indian Sign Language Recognition*

The subtitle makes the three technical domains explicit: computer vision (CV) for capture and landmark extraction, deep learning (DL) for isolated-sign classification, and IoT for approved physical responses.

## 2. Project objective

Build a reproducible, locally running application that recognises one isolated Indian Sign Language (ISL) gesture at a time, displays the corresponding official INCLUDE-50 English class label, and can optionally map selected accepted labels to safe IoT commands.

The first deliverable is a working college demonstration, followed by reproducible evaluation and error analysis for a research-quality report. The project does **not** claim a new deep-learning architecture. Its primary contribution is an integrated and evaluated CV + DL + IoT system, including measured latency, recognition errors, reliability, and safe command handling.

## 3. Scope decisions

### In scope for the MVP

- Indian Sign Language (ISL), using the official AI4Bharat INCLUDE-50 50-class vocabulary.
- One active user, one webcam session, one captured gesture, and one inference request at a time.
- Explicit manual capture: the user starts capture, performs one gesture, then stops/submits it for recognition.
- One prediction per valid capture using the selected pretrained checkpoint, with no initial training or fine-tuning.
- Show the latest accepted or rejected result, its confidence when available, recognition status, and a separate history of previous attempts.
- Stream webcam frames from the browser to a local FastAPI backend over WebSockets. Perform MediaPipe extraction and model inference on the backend.
- Preserve the established 134-feature and 169-frame model input contract.
- Keep raw webcam video out of persistent storage by default.
- Store structured recognition and IoT command events in a local SQLite database.
- Keep a separate backend IoT command router. Only exactly two INCLUDE-50 labels selected by the project owner may be mapped; the shipped map is empty. The application-level pre-IoT lock remains enabled, so no hardware command can be sent in this release.
- Use a local MQTT broker (for example Mosquitto) for development and a simulated ESP32 subscriber before physical hardware is acquired.
- Use a software-ready validation gate before any physical hardware connection.

### Explicitly out of scope for the MVP

- Continuous sentence-level sign-language recognition or natural-language sentence generation.
- Automatic gesture-start/end segmentation or continuous sliding-window prediction in the web MVP.
- Appending recognised words into a sentence or editable text string (future scope); the MVP shows the latest word and a separate event history.
- Running the full current MediaPipe + Transformer recognition pipeline directly on an ESP32.
- Multi-user or concurrent webcam sessions.
- More than the official 50 INCLUDE-50 classes in the main model.
- Initial model training, fine-tuning, comparison of many architectures, or unsupported novelty claims.
- Relays, mains-powered appliances, or higher-power actuation in the first hardware integration.

### Future roadmap

1. Add optional editable text accumulation of confirmed isolated words.
2. Investigate continuous prediction or automatic gesture segmentation after the manual-capture pipeline is reliable.
3. Experiment with resampling long sequences to the model's 169-frame input only through a controlled evaluation against the preserved baseline.
4. Add user-defined commands (for example, a custom `LED_ON` gesture) using a separate custom-command classifier first. Do not expect the existing 50-class model to recognise a new class simply because a label is added to a dictionary.
5. Consider expanding or fine-tuning the main model only if a labelled dataset, controlled protocol, and evaluation demonstrate a justified need.
6. Investigate moving inference onto a more capable edge computer. Keep the ESP32 as the actuator/controller initially; on-device inference is a future goal, not a current capability claim.

## 4. System architecture

### Target MVP architecture

```text
Browser on the Windows laptop
  ├─ getUserMedia webcam capture
  ├─ Start / Stop or Submit capture controls
  ├─ current prediction, confidence, status, event history
  └─ native WebSocket client
            │ timestamped frames while capture is active
            ▼
Local FastAPI backend (localhost)
  ├─ WebSocket frame validation and per-capture JPEG buffer
  ├─ sequential post-Stop MediaPipe Hands + Pose processing
  ├─ 134-D feature extraction and sequence validation
  ├─ verified INCLUDE-50 preprocessing
  ├─ one pretrained-model inference per valid capture
  ├─ confidence/allowlist/IoT enablement checks
  ├─ SQLite recognition and command-event history
  └─ optional MQTT publisher (locked off in this pre-IoT release)
            │ approved command with unique command ID
            ▼
Local MQTT broker (e.g. Mosquitto)
            │ local network when hardware is acquired
            ▼
ESP32 subscriber/controller
  ├─ validate topic, payload, command and command ID
  ├─ reject unapproved or duplicate commands
  └─ drive one verified low-voltage LED in the first hardware test
```

### Current capture implementation note

The primary web application buffers JPEG frames for the active manual capture and processes them sequentially after Stop. It does **not** currently run a bounded asynchronous frame queue or continuous classifier inference. A separate landmark-preview WebSocket is diagnostic UI only; it is not the classifier input stream. The `ISL_FRAME_QUEUE_SIZE` setting is retained for compatibility but is not evidence that the primary capture path uses a queue. Capture-path profiling and any queue redesign are Phase 4 work, not a claimed existing capability.

### Runtime and access boundary

- Research and dataset work can remain in Google Colab.
- The target MVP runtime is the user's Windows laptop. **Local model inference has not yet been verified on that laptop.**
- The web frontend and FastAPI backend are local-only through `localhost` for the MVP. Do not expose the development backend to the LAN or internet by default.
- MQTT is a separate network service. When the ESP32 is acquired, it may connect to the laptop's broker over the local network using suitable authentication, topic restrictions, and firewall rules.
- The MVP supports one active session/inference at a time.

### Frontend and backend decision

- Frontend: plain HTML, CSS, and JavaScript; browser camera API; native WebSocket.
- Backend: FastAPI.
- Browser-to-backend transport: WebSocket frame streaming during manually controlled capture.
- Storage: SQLite.
- MQTT broker: local broker for development and simulated-device validation.
- Streamlit was recommended in the original project specification but is **superseded** by this decision. The web UI and WebSocket integration are implemented in `src/isl_recognition/webapp.py` and `src/isl_recognition/static/`; target Windows/model end-to-end execution remains unverified.

## 5. Dataset and model decisions

### Dataset

- Dataset: AI4Bharat INCLUDE-50.
- Vocabulary: exactly 50 classes for the MVP. Use the official label map and original label ordering; do not create a manual class ordering.
- Official manifest counts recorded in the project materials: 689 training entries, 77 validation entries, and 192 test entries (958 manifest entries total).
- Historical availability audit: 872 videos reportedly located and 86 reportedly unavailable. These counts are **provisional historical findings pending a reproducible re-audit**; do not treat them as freshly verified in this package.
- The earlier estimate of 178 accessible test videos is also provisional until the availability audit is reproduced.
- Distinguish manifest entries, accessible recordings, successfully extracted feature sequences, successful inference samples, and failures in every report.

### Selected initial checkpoint

Use the following as the MVP baseline **only after successful local reproduction**:

```text
include50_no_cnn_transformer_small.pth
```

- Model family: official AI4Bharat INCLUDE-50 no-CNN Transformer-small implementation.
- Recorded configuration: hidden size 256, 4 attention heads, 2 Transformer layers, 50 outputs.
- Input tensor: `(1, 169, 134)`.
- Expected model output: `(1, 50)`.
- No training from scratch or initial fine-tuning.
- Keep the official repository and checkpoint outside the application source tree. Configure paths through `AI4BHARAT_REPO` and `ISL_CHECKPOINT`; do not commit large checkpoint files or credentials to Git.
- The selected checkpoint is not included in the supplied project ZIP. It must be obtained from the official source and checked before the local pipeline can run.

### Evidence and its limits

The supplied package records the following single-sample probe result in `experiments/2026-09-30_no_cnn_probe.json`:

- Probe: `Places/23. Court/MVI_3612.MOV`.
- Ground truth: `court`.
- Prediction: `court` (class index 8).
- Confidence: 0.8873 (88.73%).
- Input shape: `(1, 169, 134)`; output shape: `(1, 50)`.
- The notebook records a strict checkpoint state-dict load with 40 parameters, zero missing keys, and zero unexpected keys.

This is evidence of a recorded, correct single-sample prediction—not dataset-wide accuracy. The notebook's final inference cell has no saved execution output in the supplied notebook, and the checkpoint is not included in the ZIP. The result therefore still needs a reproducible rerun from the model assets, especially on the target Windows environment.

The older `include50_transformer_small.pth` checkpoint and its historical 76.10% `court` confidence must remain separate. Do not compare 76.10% and 88.73% as model-quality metrics: they are different checkpoint results on one sample. The current baseline was selected based on its place in the current implementation and strict-load evidence, not because one confidence score is higher.

### Checkpoint provenance

Before making dataset benchmark claims, inspect official checkpoint documentation, training configuration, and dataset split definitions. First evaluate the complete official 77-item validation split for baseline diagnosis and threshold review. Keep the official test split locked until the checkpoint, preprocessing, capture settings and thresholds are frozen; then run one final test evaluation and qualify claims if independent held-out use cannot be established. Add a separate external signer evaluation if feasible. Keep offline dataset evaluation distinct from live webcam/system evaluation.

## 6. Preprocessing and temporal contract

The current model input contract must not change silently.

### Per-frame representation

- First 25 pose landmarks from the 33-point pose output.
- Two hands, 21 landmarks per hand.
- X and Y coordinates only; no face features, Z coordinate, or 543-point holistic representation.
- Feature layout: pose x/y (50 values) + hand 1 x/y (42 values) + hand 2 x/y (42 values) = 134 features per frame.
- Recorded scaling semantics: x × 1920, y × 1080.
- Missing coordinates are represented as missing values during extraction and interpolated over time during preprocessing. Inputs to the model must be finite float32 values.
- Preserve the current hand-order policy (`verified_one_hand_fix`) as the baseline; detector order for two hands and alternative wrist-nearest ordering require separate evaluation, not silent changes.

### Sequence policy

- Model sequence length: 169 frames.
- Existing preprocessing truncates sequences longer than 169 and zero-pads shorter sequences to 169. Zero-padding is not interpolation.
- In the current manual web app, the server accepts at most 169 JPEG frames for one capture. After Stop, it extracts features sequentially; shorter valid feature sequences use the established zero-padding path.
- Do not resample to 169 frames in the MVP. Resampling the full gesture is a future controlled experiment.
- Start with a configurable target near 25 FPS because the verified probe video was 25 FPS. This is a provisional engineering setting, not proof that all dataset videos use 25 FPS or that the local pipeline can sustain it.
- Preserve and validate frame timestamps. The current primary capture implementation stores accepted JPEGs in a bounded per-capture list; browser-side WebSocket backpressure may skip sends. Profile the current path before introducing an asynchronous queue or changing frame sampling.

## 7. Capture, prediction, and UI behaviour

1. The user explicitly enables the webcam and starts a single gesture capture.
2. The browser streams timestamped frames to the local FastAPI backend over WebSocket.
3. The backend validates and buffers accepted JPEG frames in memory for this capture. No raw video is persisted by default.
4. The user stops/submits capture. The backend creates a MediaPipe session, extracts landmarks from buffered frames sequentially, then validates feature-frame count, landmark availability, sequence integrity, and missing-data severity using configurable provisional criteria.
5. Empty, too-short, or unusable captures are rejected before inference, with a clear retry message and recorded reason.
6. Valid sequences follow the established interpolation/scaling/padding policy. The current web MVP stops accepting JPEG frames at 169 captured frames; post-Stop extraction then creates the feature sequence for preprocessing.
7. Perform exactly one model inference per valid capture.
8. Display the official predicted English class label, confidence, and status. This is isolated-word classification—not sentence-level translation.
9. The main display shows the latest result. A separate event-history panel retains prior attempts; editable text accumulation is future scope.
10. The current web MVP uses `ISL_CONFIDENCE_THRESHOLD=0.40` as a **provisional, uncalibrated text-recognition threshold**; do not call it optimal. The separate IoT command gate defaults to `ISL_IOT_MIN_CONFIDENCE=0.70` and is not calibrated either. The five-prediction history belongs to the experimental continuous/sliding-window runner/future mode; it is not temporal stabilisation in the one-inference-per-capture MVP.
11. Reject low-confidence predictions with an explicit status/retry message. Confidence thresholding does not guarantee detection of every out-of-vocabulary gesture.
12. Invalid capture, inference failure, or rejected prediction must never trigger IoT.

## 8. IoT command behaviour and safety

### First intended demonstration

- The project owner will select exactly two official INCLUDE-50 labels for IoT actions. Until those choices are made, the mapping file remains empty and commands cannot be produced. This is an application mapping only; it does not change the model vocabulary or class order.
- IoT actuation is hard-locked in the consolidated pre-IoT release. The UI/API cannot enable it and the backend suppresses commands. Enabling it requires a separate reviewed IoT release after validation gates pass.
- Only accepted predictions on an allowlist can cause a command.
- At most one command may be issued for a capture. A cooldown may also be applied.
- The ESP32 must independently validate the topic, command payload and command ID, and reject unapproved or duplicate commands.
- Give each command a unique command ID. Track publish status separately from device acknowledgement. UI statuses: `pending`, `acknowledged`, `rejected`, `timed out`, and `publish failed`.
- An acknowledgement means the ESP32 reports that it validated/processed the command; it is not independent proof that the LED physically changed state.
- Do not automatically republish an uncertain actuation. Handle duplicate IDs idempotently on the device.
- Local broker authentication where supported, topic restrictions, credentials outside source control, and appropriately restricted firewall/network access are required before using real hardware.
- If MQTT is unavailable, text recognition may continue, but the command must not be reported as successful. Log the failure. Do not auto-retry in a way that might actuate twice.

### Current protocol status and historical code

The older `src/isl_recognition/mqtt_client.py` and its `isl/stable_word` raw-word protocol are historical/superseded and are not used by the new web application. The web application uses `src/isl_recognition/mqtt_gateway.py` with the versioned command/ack contract in `src/isl_recognition/iot_protocol.py`; `scripts/run_mqtt_simulator.py` simulates the subscriber and `esp32/isl_mqtt_endpoint.ino` implements the matching protocol. Unit tests cover the deterministic schema/gates/simulator logic. A live Mosquitto broker and physical ESP32 have not been tested in this environment, so these assets are not approved for physical use until the software-ready and electrical-safety gates below pass.

### Hardware gate

No physical ESP32 or output components have been confirmed/acquired yet. Finish software recognition and MQTT simulation first. The first physical test, after the exact board and components are identified, must use one low-voltage LED only. Verify the board's GPIO limits, LED polarity, current-limiting resistor, power source and wiring before applying power. Do not use relays or mains appliances in this phase.

## 9. Software, local runtime, and packaging

- Research environment: Google Colab may remain for notebook experiments and dataset processing.
- Target application environment: the user's Windows laptop, local-only at `localhost`.
- Test a separate clean Python 3.12 environment with the exact MediaPipe + PyTorch + Transformers + checkpoint pipeline. Preserve validated environments and artifacts unchanged while testing. If the unified environment fails, use separate Python processes/services rather than making random package changes.
- Model-side notebook environment was Python 3.13 with PyTorch 2.11.0, Transformers 4.46.3, and Tokenizers 0.20.3. MediaPipe extraction was validated separately with Python 3.12, MediaPipe 0.10.21, and OpenCV 4.11.0.86. This is the documented historical setup, not proof of a working combined Windows environment.
- Prefer compatible NVIDIA GPU inference if local CUDA, checkpoint loading and prediction checks pass; retain CPU fallback. Measure actual Windows performance. GPU compatibility is not yet verified locally.
- Frontend: plain HTML/CSS/JavaScript, FastAPI backend, and WebSocket frame streaming are implemented. Their target Windows/local webcam/model end-to-end path is not yet independently verified.
- Provide Windows setup instructions, compatible/pinned dependency versions, `.env.example`, configuration checks, and a clear startup workflow. Finalise exact dependency pins after the target environment is tested.
- `AI4BHARAT_REPO` and `ISL_CHECKPOINT` configure external model assets. Keep assets and secrets out of Git.
- Web app starts at `localhost`; do not open it to the LAN/internet by default.

## 10. Event history and persistence

Use a local SQLite database for structured events. Data must survive browser refresh and backend restart. Document retention and reset controls. Do not persist raw webcam video by default.

Recognition history should record timestamp, predicted label when available, confidence, processing latency, and outcome (`accepted`, `rejected`, `invalid capture`, or a documented failure status). IoT command events must be stored separately, including command ID, mapped command, publish result, acknowledgement/result, timeout/rejection/failure, and timestamps. Viewing history must never re-execute an old command.

## 11. Error handling and reliability

- If model initialisation fails, recognition is unavailable and the UI shows an error.
- If capture validation or inference fails, do not accept a prediction or send an IoT command.
- If MQTT is unavailable, recognition can continue; record command failure and do not claim success.
- Keep components isolated and fail safely. No automatic retry may repeat a physical action; command IDs and device-side idempotency must protect against duplicates.
- Unknown/unfamiliar gestures may still be classified as one of 50 known classes. The MVP uses quality and confidence rejection, but does not claim a validated out-of-vocabulary detector.

## 12. Evaluation plan

### A. Model evaluation

- Verify checkpoint training provenance and official split definitions first.
- First evaluate all 77 items in the official validation split to diagnose the baseline and review thresholds. Keep the official test split locked until checkpoint, preprocessing, capture settings and thresholds are frozen; then run one final test evaluation and report manifest totals, accessible data, successful inferences, and failures separately.
- Report top-1 accuracy, precision/recall where appropriate, macro F1, weighted F1, confusion matrix, and per-class results.
- Do not change thresholds based on held-out test results. The current web recognition threshold (0.40) and separate IoT threshold (0.70) are uncalibrated defaults; manual MVP uses one inference per capture, so the five-prediction history is not a temporal stabiliser in this mode.
- The historical availability audit (872 found / 86 missing; 178 accessible test videos) must be reproduced before being used as final counts.

### B. Live system evaluation

Keep this separate from dataset metrics. Measure:

- capture duration;
- browser frame cadence, frames received, client-side dropped frames, and buffered-frame count;
- MediaPipe processing latency;
- model inference latency;
- post-capture processing and capture-to-text latency;
- MQTT publish-to-acknowledgement timing when implemented;
- end-to-end time to reported device acknowledgement;
- effective FPS, CPU/GPU/RAM use where feasible;
- prediction acceptance/rejection, capture failures, and stability;
- command allowlist behaviour, duplicate rejection, disconnect/reconnect, timeout and recovery.

A successful single `court` inference is not overall accuracy. Simulated ESP32 results are not physical hardware results. An MQTT publish return value is not proof of device execution.

## 13. Research contribution and claims

The guaranteed contribution is a reproducible and evaluated end-to-end system integrating pretrained isolated-ISL classification, computer-vision landmark extraction, a local browser/backend application, and a safely gated IoT command path. Research analysis focuses on latency, reliability, classification errors, capture-quality rejection, and command execution behaviour.

Do not claim a novel deep-learning architecture. Do not claim continuous sentence recognition, on-ESP32 inference, physical hardware operation, dataset-wide accuracy, optimised thresholds, or benchmark independence unless those claims are demonstrated and documented.

## 14. Current repository status

### Implemented in the current implementation package

- FastAPI application with startup/shutdown lifecycle, readiness/health endpoint, SQLite APIs and manual WebSocket capture flow.
- Plain HTML/CSS/JavaScript camera UI, manual Start/Stop/Cancel, progress/status, latest result, and separate recognition/IoT histories.
- Primary capture path buffers JPEG frames in memory, validates timestamps and payload size, applies a one-active-capture lock and 6-second/169-frame limits, then sequentially extracts landmarks after Stop. It is not a bounded asynchronous frame queue.
- MediaPipe extractor adapter wired to the established 134-D feature representation and 169-frame preprocessing; actual landmark extraction is not verified here because MediaPipe is absent.
- Official INCLUDE-50 model adapter that requires configured repository/checkpoint and strict state-dict loading; it does not silently substitute a fake model.
- SQLite persistence for recognition outcomes and separate IoT command/ack events.
- Versioned MQTT command/ack schema, empty-by-default exactly-two-label IoT map, independent IoT confidence/acceptance/cooldown checks, health reporting, enable guard and deterministic Python simulator. The application-level pre-IoT lock remains on.
- ESP32 sketch with two compile-time label/command allowlist pairs; empty defaults reject commands and `LED_GPIO` defaults to -1.
- Pinned 77-item official validation-manifest tooling, missing-only validation downloader and validation-first evaluator with explicit test-split lock.
- Windows Phase 0 backup/Git and local validation workflow scripts; `.env.example`, updated implementation plan, worklog, release manifest and engineering decisions.

### Evidence recorded versus tested now

- Historical strict-load and one-sample evidence remains historical only: 40/40 parameter keys loaded in an earlier recorded experiment; `court` → `court` at 0.8873 confidence. It was not reproduced in this work environment.
- 7 Nov 2026 Phase 0 audit regression suite: 160 passed, 1 skipped in the review container (Python 3.13.5 / pytest 9.0.2; not target Windows). The skipped test requires the external AI4Bharat repository and checkpoint. Test doubles do not validate the official checkpoint. Backend capture-idempotency now survives recognition upserts and visible history reset/retention through a separate SQLite ledger.
- The official `include50_val.txt` source snapshot parsed as 77 unique paths representing all 50 labels and matched the pinned SHA256. The exported source snapshot lacked `.git` metadata; the user's complete local Git clone must pass the commit check.
- A full 77-video validation inference, target Windows webcam, live broker, ESP32 compile and physical action have not been performed.

### Not verified in this environment

- Windows/Python 3.12 compatibility of the combined model + MediaPipe environment and strict load/inference using real model assets.
- Live webcam capture, NVIDIA GPU/CPU benchmark, and actual full browser → MediaPipe → model → UI latency.
- Live Mosquitto/Paho publish-and-ack integration, reconnect/timeout on a broker, Arduino compilation, or physical ESP32/LED operation.
- Reproducible dataset availability audit, full INCLUDE-50 test metrics, independent test split/provenance, and research-level benchmark claims.

The existing `scripts/run_live.py` is a continuous/sliding-window experimental runner with periodic inference and a stabiliser. It is not the selected manual one-inference-per-capture web MVP. Keep it as a debugging/experimental tool unless intentionally updated and validated.

## 15. Required software-ready gate before hardware

Do not connect physical hardware until all applicable software checks pass:

1. Local Windows environment loads the configured official repository and checkpoint and reproduces the documented probe.
2. Automated unit tests pass; model-dependent skips are understood and run with model assets where possible.
3. Manual web capture → WebSocket → MediaPipe → validated 134-D frames → established preprocessing → one inference → UI result works end to end.
4. Invalid/empty/short/unusable captures and inference failures are rejected, logged and cannot trigger IoT.
5. SQLite history persists across browser refresh and backend restart; retention/reset controls are documented.
6. MQTT simulation runs against a local broker and simulated ESP32 subscriber using the final command schema.
7. Tests cover allowed and disallowed commands, user-disabled actuation, low confidence, one-action-per-capture, duplicate IDs, acknowledgement statuses, timeout, broker disconnect, recovery, and no duplicate physical action on uncertain publish.
8. Latency measurements are collected and reported honestly; GPU and CPU paths are tested where feasible.
9. Setup, startup and configuration steps are reproducible from documentation.

Physical integration is a separate gate: identify the exact board and components, verify electrical limits and wiring, then test a single low-voltage LED. No relay or mains appliance is permitted in the initial hardware test.

## 16. Documentation governance and superseded decisions

- `PROJECT_CONTEXT.md`: authoritative current context and decisions.
- `README.md`: concise entry point and current status.
- `PROJECT_SPEC.md`: requirements and acceptance criteria consistent with this context.
- `IMPLEMENTATION_PLAN.md`, `IMPLEMENTATION_WORKLOG.md`, and `RELEASE_MANIFEST.json`: current engineering plan, changes and evidence.
- `docs/HANDOFF.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/RUNBOOK.md`: operational details/status aligned to this context.
- `docs/EXPERIMENT_LOG.md`: historical experiment record; preserve original experiment entries and dates.

Explicitly superseded decisions:

- Streamlit for the MVP → **FastAPI + plain JavaScript + WebSockets**.
- Continuous/sliding-window prediction as the MVP → **manual capture and exactly one inference per valid capture**.
- Treating the five-prediction history as MVP stabilisation → reserved for future continuous mode.
- Assuming the 872/86 availability audit is final → **provisional until reproduced**.
- Any implication that current MQTT/ESP32 source is hardware-ready → **not true; protocol/simulator code exists, but live broker, firmware compile and physical hardware gates remain untested**.



## User-facing interpretation and vocabulary guide update — 2026-10-26

The web UI now exposes `GET /api/vocabulary` using the validated official INCLUDE-50 label map and provides a searchable 50-class guide. It displays zero-based model indices explicitly, friendly spacing for a few concatenated class names while retaining each exact official label, and top-five model candidates when the model returns them. Candidate scores are labelled uncalibrated. `accepted` means only that the configurable threshold was passed; it is not independently verified correctness. `rejected` must be shown as a low-score candidate, not confirmed recognition.

The application deliberately does not invent sign illustrations or motion instructions. The official class map provides English class glosses and indices, not a sign-language tutorial. Reference video demonstrations require the separate INCLUDE dataset media or a verified ISL educator resource; the dataset media are not bundled with the application. This UI improvement does not expand the classifier beyond the fixed 50 classes or change the one-isolated-gesture MVP scope.

---

## Phase 1 evidence and current next gate — 7 November 2026

The user's target-Windows Phase 1 paired run is complete and retained under `engineering/phase1_evidence/`. Browser Canvas achieved 44/77 top-1 (57.14%), 67/77 top-5 (87.01%), macro F1 0.5207; OpenCV native reference achieved 46/77 top-1 (59.74%), 69/77 top-5 (89.61%), macro F1 0.5594. The two top-1 predictions agreed on 66/77 examples. All 77 browser capture containers were reported received. These are descriptive results on the official validation split with low per-class support, not external-signer generalisation or a production-ready result.

The next gate is Phase 2 target-Windows four-path feature provenance ablation. Preserve all locked model and IoT settings. Use the complete runbook in `engineering/PHASE2_FEATURE_PROVENANCE_RUNBOOK.md` and retain its disk logs because console scrollback may truncate.
