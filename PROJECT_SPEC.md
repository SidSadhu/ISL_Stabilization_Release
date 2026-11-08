# Project Specification

> **Source of truth:** [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md). This file defines implementation requirements and acceptance criteria. Where older text conflicts, the current master context wins.

## 1. Objective and identity

**Official title:** Develop a Real-Time Deep Learning-Based System for Recognising Isolated Sign Language Gestures and Converting Them into Text.  
**Subtitle:** Computer Vision, Deep Learning and IoT-Based Physical Response for Indian Sign Language Recognition.

Build and evaluate a local CV + DL + IoT system for isolated ISL gesture classification, using the official pretrained AI4Bharat INCLUDE-50 model and a safe, optional command path to an ESP32. The research contribution is the integrated system and its evaluation, not a claimed novel architecture.

## 2. MVP requirements

1. Use exactly the 50 official INCLUDE-50 classes and the official label map/order.
2. Use `include50_no_cnn_transformer_small.pth` as the initial checkpoint candidate, only after local reproduction. No initial training/fine-tuning.
3. Preserve the 134-D, 169-frame model input contract and current AI4Bharat preprocessing.
4. Implement a manual Start/Capture/Stop-or-Submit sequence and exactly one inference per valid capture.
5. Stream timestamped webcam frames from browser to FastAPI over WebSockets. The current primary path buffers frames for one manual capture and extracts them sequentially after Stop; do not describe it as an existing bounded asynchronous queue.
6. Validate capture quality before inference. Invalid input or low-confidence output cannot cause an IoT command.
7. Show the latest official English class label, confidence, and status. Store prior attempts in a separate history. Do not implement sentence translation in the MVP.
8. Store recognition and IoT command events in SQLite. Do not store raw webcam recordings by default.
9. Build the frontend in plain HTML/CSS/JavaScript. Use FastAPI for the backend and local WebSocket communication. Streamlit is superseded.
10. Target Windows laptop local execution (`localhost`), with GPU preferred only when compatible and CPU fallback available. The Windows runtime remains unverified in this build environment; see the completion report.
11. Provide a local MQTT broker path and simulated ESP32 subscriber before physical hardware becomes available.
12. Keep the full 50-class recogniser. A separate IoT allowlist may contain exactly two owner-selected official labels; the default map is empty. At most one action per capture, with a separate IoT confidence gate.
13. Track each command with a unique ID, publish result and device acknowledgement; support idempotent duplicate handling and do not blindly retry uncertain actuation.
14. Fail safely: model/capture/inference failures produce no accepted prediction or IoT action. MQTT failure must not stop text recognition but must be recorded honestly.
15. Do not begin physical wiring until the software-ready gate in `PROJECT_CONTEXT.md` passes.

## 3. Fixed model contract

| Item | Current baseline |
|---|---|
| Dataset/vocabulary | AI4Bharat INCLUDE-50, 50 classes |
| Per-frame representation | 25 pose points + 2 × 21 hand points, X/Y only |
| Feature dimension | 134 |
| Coordinate scaling | X × 1920, Y × 1080 |
| Missing values | Temporal interpolation; final model input finite float32 |
| Temporal length | 169 frames |
| Short sequence | Zero-pad to 169 |
| Long offline sequence | Existing preprocessing truncates to 169 |
| Live manual capture | Show progress and stop accepting new JPEG frames at 169 captured frames; post-Stop extraction then follows the established 134-D/169-frame preprocessing contract |
| Model | Official AI4Bharat INCLUDE no-CNN Transformer-small candidate |
| Recorded architecture | Hidden size 256, 4 heads, 2 layers, 50 outputs |
| Input/output | `(1,169,134)` → `(1,50)` |
| Web recognition threshold | 0.40 provisional and uncalibrated; do not tune until the complete official validation split is evaluated |
| IoT minimum confidence | 0.70 provisional, independent from text recognition, and still subject to the hardware release gate |

Any alternate temporal resampling or hand-order policy is an experiment and must be compared against the preserved baseline before adoption.

## 4. Dataset and evidence safeguards

- Official manifest totals recorded: 689 train, 77 validation, 192 test (958 total entries).
- The historic 872-available / 86-missing audit and 178 accessible-test estimate are provisional until a reproducible availability audit is completed.
- The experiment JSON records one `court` → `court` prediction at 88.73% confidence using the no-CNN checkpoint. This is a single-sample probe, not accuracy. The older 76.10% result belongs to a different historical checkpoint and must not be treated as a fair model comparison.
- Verify checkpoint provenance and split definitions before benchmark claims. Report manifest, available, processed, successful, and failed samples separately.
- Dataset evaluation and live-system evaluation are separate tracks.

## 5. IoT protocol and hardware requirements

The new web application and ESP32 sketch use the documented versioned command/ack protocol in `docs/PROTOCOL.md`. Model-independent schema/gating tests are included. Live broker integration, Arduino compilation and hardware behavior have not been tested in this environment. The legacy raw-word `isl/stable_word` transport is superseded and not used by the web application.

The first physical test is one low-voltage LED only. Verify the exact ESP32 board, GPIO electrical limits, LED polarity, current-limiting resistor, power source, and wiring. No relay or mains appliance in the initial integration.

## 6. Evaluation acceptance

### Model

- top-1 accuracy, precision/recall where appropriate, macro F1, weighted F1;
- confusion matrix and per-class outcomes;
- checkpoint provenance/split-independence caveats;
- unavailable/failed records documented.

### System

- capture duration and delivered/processed frame rate;
- buffered-frame count, client-side dropped-frame counts, MediaPipe extraction time and Stop-to-result latency;
- MediaPipe, model and capture-to-text latency;
- CPU/GPU/RAM measurements where feasible;
- capture rejection, confidence rejection, and prediction errors;
- MQTT publish/ack time, accepted/rejected command, timeout, disconnect/recovery, duplicate command handling;
- software simulation results kept separate from physical hardware results.

## 7. Software-ready gate

Before physical hardware: reproduce local checkpoint inference; pass unit and integration tests; prove manual capture-to-result over FastAPI/WebSocket; validate SQLite persistence; test safe command handling and MQTT simulation including failure cases; record local latency; test setup/start scripts; document unresolved issues. See `PROJECT_CONTEXT.md` for the complete gate.

## 8. Future scope

- Editable text accumulation.
- Continuous prediction or automatic gesture segmentation.
- Controlled experiment comparing 169-frame resampling with current truncation.
- Separate trained custom-command classifier for gestures such as `LED_ON`.
- Main-model class expansion/fine-tuning only with data and a justified experiment.
- More capable edge inference; full current pipeline on ESP32 is not the MVP.


## 9. Implementation and test status

See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), [`IMPLEMENTATION_WORKLOG.md`](IMPLEMENTATION_WORKLOG.md), [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md), and [`RELEASE_MANIFEST.json`](RELEASE_MANIFEST.json) for current status and evidence boundaries. Core tests use mock model/extractor boundaries where external assets are unavailable; mock-model tests are not evidence of actual checkpoint inference.
