# Architecture and Data Flow

## Runtime topology

```text
Browser (127.0.0.1:8000)
  ├─ getUserMedia preview; manual start/stop/cancel
  ├─ JPEG frames + Unix timestamps as JSON/base64 over native WebSocket
  ├─ latest isolated-word result + current capture telemetry
  └─ separate SQLite-backed recognition and IoT histories
             │  ws://127.0.0.1:8000/ws/capture
             ▼
FastAPI process
  ├─ readiness and model-asset checks (never fake-model fallback)
  ├─ one-active-capture lock
  ├─ bounded per-capture JPEG list (max 169 frames / max capture duration)
  ├─ MediaPipe Hands + Pose in a session-affine worker thread
  ├─ 134-D features; provisional pose/hand/extraction quality checks
  ├─ preprocessing to finite float32 (169,134) sequence
  ├─ one real model inference per valid manual capture
  ├─ 0.40 uncalibrated text threshold; independent 0.70 IoT gate
  ├─ SQLite persistence
  └─ fail-closed MQTT publisher + acknowledgement listener (locked off for this release)
             │ `isl/command/v1` + unique command ID
             ▼
Local Mosquitto broker (example binds 127.0.0.1 only)
             │
             ▼
Simulated ESP32 (Python) / future real ESP32 firmware
  ├─ independent protocol, expiration, allowlist and duplicate validation
  ├─ simulation: log a blink, never physical GPIO
  └─ ACK on `isl/ack/v1`
```

## Service boundaries

- `app_config.py`: environment and `.env` settings; loopback-only host validation.
- `feature_extractor.py`: lazy MediaPipe/OpenCV extraction adapter, reusing existing landmark ordering/scaling decisions.
- `preprocess.py` and `model.py`: preserved model tensor contract and official INCLUDE transformer adapter.
- `webapp.py`: FastAPI lifecycle, health API, history APIs, WebSocket state machine and server-side actuation gates.
- `storage.py`: SQLite schema, independent recognition/IoT histories, ack transitions, timeout handling and retention/reset.
- `iot_protocol.py`: command JSON v1.0, allowlist, validity/expiry checks and deterministic simulator logic.
- `mqtt_gateway.py`: optional Paho integration. It never retries uncertain physical actions automatically.
- `scripts/evaluate_dataset.py`: validation-first official-split evaluation; test split requires an explicit final-evaluation flag; requires actual data/model assets.
- `scripts/benchmark_runtime.py`, `scripts/benchmark_system.py`: report actual model input latency and captured local runtime metrics respectively.

## Capture lifecycle

`start` acquires the single-process lock and creates the capture state. The browser emits JPEG frames with timestamps while the user is recording. The backend validates each message and stores accepted JPEG bytes in a per-capture in-memory list. The primary web capture path does not use a bounded asynchronous frame queue and does not drop accepted server-side frames under queue overload. The browser may skip sending frames when its WebSocket buffered amount is high; that client-side dropped count is reported at Stop. After `stop`, the backend creates a MediaPipe session, sequentially extracts landmarks from each buffered frame, reports extraction progress, validates the resulting features, closes the session, and performs exactly one inference for a valid capture. `cancel` and disconnect discard the unfinished capture. Valid captures are checked before exactly one inference call. Low confidence and all errors are persisted without an IoT action.

## Evidence and limitation notes

The capture API and model interface are tested using test doubles. The official model weights, Windows runtime, live camera, live Mosquitto, ESP32 compilation and hardware action are external gates and are not represented as passed by the current completion report.
