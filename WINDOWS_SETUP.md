# Exact Windows Setup — ISL INCLUDE-50 Project

This guide targets **Windows 10/11, 64-bit Python 3.12, and PowerShell**. The application is local-only by default and must bind to `127.0.0.1`, not `0.0.0.0`. The official checkpoint and dataset are not bundled.

## 1. Extract and open PowerShell

Extract `ISL_DL_CV_IoT_EndToEnd.zip`, then open PowerShell in the extracted `ISL_Recognition_Project` folder. Confirm the project root contains `PROJECT_CONTEXT.md`, `requirements-web.txt`, and `scripts`.

```powershell
py -3.12 --version
Set-ExecutionPolicy -Scope Process Bypass
```

If `py -3.12` fails, install 64-bit Python 3.12 and Git, reopen PowerShell, and retry.

## 2. Install project dependencies

Full setup (recommended when attempting model/MQTT integrations):

```powershell
.\scripts\setup_windows.ps1
```

The script creates `.venv`, installs the pinned/limited dependency files, copies `.env.example` to `.env` if needed, and runs preflight. The combination has **not** been verified on a Windows machine by this build. If you already have the project Python 3.12 `.venv`, keep it and do not recreate it; use its `python.exe` directly. If you are performing a fresh installation, use the setup script below. If a package installation fails, keep the complete error and do not randomly upgrade/downgrade Torch/Transformers. To install only the local UI/health-service dependencies first:

```powershell
.\scripts\setup_windows.ps1 -CoreOnly
```

Use the project-local Python explicitly in later commands:

```powershell
.\.venv\Scripts\python.exe .\scripts\preflight.py
.\.venv\Scripts\python.exe -m pytest -q
```

The UI and health API can start while the app reports `degraded`, but recognition requires the official model checkpoint and MediaPipe.

## 3. Configure the official external model assets

The repository and checkpoint must remain outside the application source directory. The official AI4Bharat repository contains the INCLUDE implementation and its `pretrained_links.json` index.

```powershell
New-Item -ItemType Directory -Force C:\AIAssets | Out-Null
git clone https://github.com/AI4Bharat/INCLUDE.git C:\AIAssets\INCLUDE
New-Item -ItemType Directory -Force C:\AIAssets\checkpoints | Out-Null
Invoke-WebRequest `
  -Uri 'https://api.wandb.ai/files/abdur-ai4bharat/include50-no-cnn/11d20bb9/augs_transformer.pth' `
  -OutFile 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth'
Get-Item 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth' | Select-Object FullName,Length
```

The download target above is the one listed for `include50_no_cnn_transformer_small.pth` in the official index when this package was prepared. This build **did not download, checksum-verify, or load it**. If the link is unavailable, consult the official `pretrained_links.json` index and use its current entry; do not use an unverified mirror.

Edit `.env` (create it from `.env.example` if needed) so these values are set. Preserve the documented provisional text threshold of `0.40`; do not change it to make predictions appear accepted.

```dotenv
AI4BHARAT_REPO=C:/AIAssets/INCLUDE
ISL_CHECKPOINT=C:/AIAssets/checkpoints/include50_no_cnn_transformer_small.pth
ISL_LABEL_MAP=config/label_map_include50.json
ISL_HOST=127.0.0.1
ISL_PORT=8000
ISL_CONFIDENCE_THRESHOLD=0.40
ISL_IOT_ENABLED=false
ISL_IOT_LABEL_MAP=config/iot_label_map.json
ISL_IOT_MIN_CONFIDENCE=0.70
```

Save the file, then run:

```powershell
.\.venv\Scripts\python.exe .\scripts\preflight.py
.\.venv\Scripts\python.exe -m pytest -q
```

The app must report `model_ready: true` and `extractor_ready: true` at `http://127.0.0.1:8000/api/health` before camera recognition can work. Run the model-dependent checkpoint test and reproduce the historical one-sample `court` probe before describing model integration as verified. That old result is historical evidence, not an accuracy score.

## 4. Start the web app

```powershell
.\scripts\start_app.ps1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Verify health at [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health). In the browser, allow webcam access, click **Enable camera**, frame a single isolated gesture, click **Start capture**, then **Stop / recognise**. The UI is for isolated signs: it does not translate continuous signing into sentences. This consolidated pre-IoT release hard-locks IoT in application code; it cannot be enabled from the UI or API.

## 5. Future IoT simulation notes (not enabled in this release)

Install Mosquitto separately for Windows and ensure `mosquitto.exe` is available on `PATH`. Open three PowerShell windows in the project root.

**Terminal A — broker:**

```powershell
mosquitto -c .\broker\mosquitto-local.conf -v
```

**Terminal B — simulated ESP32 subscriber:**

```powershell
.\.venv\Scripts\python.exe .\scripts\run_mqtt_simulator.py
```

**Terminal C — app:**

```powershell
.\scripts\start_app.ps1
```

The pre-IoT release intentionally blocks this activation path, so these commands are for later engineering work only and cannot operate a device through this release. The simulator prints simulated actions only; it does not operate physical GPIO. The example broker configuration is loopback-only and must not be reused unchanged for ESP32 LAN deployment. If Paho/Mosquitto is missing, MQTT is unverified. A later IoT release must explicitly change the code-level lock after the recognition and threshold-validation gates pass.

## 6. Test, evaluate, and benchmark

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
.\.venv\Scripts\python.exe .\scripts\preflight.py
```

For the first full evaluation, use the pinned official **validation** split only. It verifies the upstream Git commit and split-list SHA256, writes the exact 77-item manifest, offers a missing-only download of validation videos, and runs the evaluation. It asks for the explicit text `FETCH CLIPS` before transferring video bytes:

```powershell
.\scripts\run_official_validation_workflow.ps1
```

Do not evaluate the held-out test split while selecting the model, detector, preprocessing, confidence threshold, or IoT configuration. The evaluation runner blocks test use unless `--allow-test-evaluation` is explicitly supplied after all choices are frozen. Missing files, extraction failures, attempted videos and successful inferences are counted separately; a full-split result is valid only when `evaluation_complete_for_official_split` is `true`.

For model-input latency, supply a real preprocessed `.npy` array:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_runtime.py --features-npy .\outputs\model_inputs.npy --warmup 5 --samples 50 --out .\outputs\model_benchmark.json
.\.venv\Scripts\python.exe .\scripts\benchmark_system.py --db-path .\var\isl_events.sqlite3 --out .\outputs\system_benchmark.json
```


## 7. Hardware boundary

The ESP32 sketch has not been compiled or run on physical hardware. Before any physical test, identify the exact board and GPIO electrical limits, LED polarity and resistor, confirm power/wiring, compile against that board's core and libraries, and complete the software-ready gate. Begin only with one low-voltage LED under supervision. Do not attach a relay, mains appliance, or other high-power actuator.

See `docs/RUNBOOK.md`, `docs/PROTOCOL.md`, `docs/IMPLEMENTATION_STATUS.md`, and `COMPLETION_REPORT.md` for complete details and evidence boundaries.

## Optional: reference videos for learning signs

Follow [`docs/INCLUDE_REFERENCE_VIDEOS.md`](docs/INCLUDE_REFERENCE_VIDEOS.md) to fetch the small official metadata index and, if you choose, inspect/download the separately distributed video archives. The default script only lists archive sizes; downloading all archives requires the explicit `-Download` flag and typed confirmation.
