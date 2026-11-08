# Project Build Instructions

## Source priority
1. `PROJECT_CONTEXT.md` — authoritative current scope and decisions.
2. `PROJECT_SPEC.md` — implementation requirements and acceptance criteria aligned to the context.
3. `notebooks/DL_Project_MVP_CURRENT.ipynb` and `experiments/` — historical/recorded evidence; distinguish saved outputs from code that was not executed in the saved notebook.
4. Official AI4Bharat INCLUDE repository — original model and preprocessing implementation.
5. Existing source code and tests — inspect before adding or replacing modules.

## Non-negotiable constraints
- Isolated ISL word classification only in the MVP; no continuous sentence translation.
- INCLUDE-50 and official label ordering; 50 classes for the current model.
- 134-D X/Y landmark feature representation and 169-frame model contract unless a controlled experiment justifies a change.
- Pretrained `include50_no_cnn_transformer_small.pth` is the initial baseline candidate, subject to local reproduction.
- No MVP training from scratch or initial fine-tuning.
- Do not choose checkpoint based on a single confidence score or claim a single prediction is accuracy.
- No fabricated metrics or hardware/runtime claims.
- The web MVP is manual capture and one inference per valid capture; the existing continuous runner is experimental, not the chosen web workflow.
- No physical hardware actuation until the software-ready gate in `PROJECT_CONTEXT.md` passes.
- The ESP32 is an IoT endpoint/controller in the current architecture, not the inference device.

## Architecture decisions
- Web: plain HTML/CSS/JavaScript + FastAPI + WebSockets, local `localhost` only for the MVP.
- Research may remain in Colab; target application runtime is Windows, but it is not yet verified.
- Try a clean Python 3.12 environment while preserving existing model/MediaPipe environments. If incompatible, separate processes/services are acceptable.
- Prefer a verified compatible NVIDIA GPU with CPU fallback.
- MQTT remains separate from model logic. Build a local broker + simulated subscriber first.
- Final MQTT protocol must use approved command payloads, unique command IDs, device acknowledgements and idempotent duplicate handling. Current raw-word QoS 0 scaffolding is not hardware-ready.
- IoT mapping: exactly two official INCLUDE-50 labels selected by the project owner; default mapping file is empty. The full 50-class text model is unchanged.
- SQLite event history; no raw-video persistence by default.

## Build order
1. Reproduce the current checkpoint and preprocessing locally on Windows.
2. Validate the Python 3.12 combined environment or set up isolated local processes.
3. Implement capture validation and manual one-shot inference endpoint.
4. Implement local FastAPI/WebSocket backend and plain-JavaScript frontend.
5. Implement SQLite event history and reliability/error reporting.
6. Implement safe MQTT command protocol and simulator.
7. Pass the software-ready gate.
8. Perform model/data evaluation and live-system benchmark separately.
9. Only then identify board/components and test one low-voltage LED.
10. Extend to robustness, explainability and future custom-command research if justified.

## Stop conditions
Stop and report the exact error if checkpoint shapes mismatch, input/output tensor shape differs, label mapping differs, preprocessing is inconsistent, or package incompatibility occurs. Do not randomly change versions or modify the official label ordering.

## Experiment discipline
For each meaningful experiment, record date/time, environment, checkpoint, data/sample, preprocessing policy, device, metrics, interpretation and limitations. Preserve historical experiment records; do not rewrite old outcomes to fit later decisions.
