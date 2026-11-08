# Windows Runbook — Setup, Models, Web App and MQTT Simulation

This runbook is for Windows 10/11 PowerShell and the project root. `PROJECT_CONTEXT.md` remains the source of truth. The application binds to `127.0.0.1`; do not change it to `0.0.0.0` for the MVP.

## 1. Prerequisites

- 64-bit Python 3.12 installed from the official Python distribution, with the `py` launcher available.
- Git for the AI4Bharat repository checkout.
- Windows PowerShell.
- Webcam and browser permission for actual camera capture.
- Optional for MQTT software simulation: Mosquitto installed locally. It is not bundled.
- Optional for hardware work: exact ESP32 board/LED components, only after the software-ready gate and electrical-safety checklist pass.

Open PowerShell in the extracted project folder and verify:

```powershell
py -3.12 --version
python --version
```

## 2. Create the environment and install dependencies

Recommended single command (installs web, MediaPipe, MQTT, model, evaluation and test requirements):

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
```

Or perform it explicitly:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-web.txt
python -m pip install -r requirements-mediapipe.txt
python -m pip install -r requirements-model.txt
python -m pip install -r requirements-mqtt.txt
python -m pip install -r requirements-evaluation.txt
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python .\scripts\preflight.py
```

The project versions represent historical model/feature-extraction environments plus a proposed unified install, not a Windows integration-test result. If a pinned dependency cannot install or model imports fail, capture the exact error before changing versions. Do not overwrite the existing environment until the alternate version is tested independently. Use a CPU-capable PyTorch install as the initial compatibility test; only enable CUDA after `torch.cuda.is_available()` and model inference both succeed. For current official Windows install guidance, see [PyTorch Start Locally](https://pytorch.org/get-started/locally/).

## 3. Obtain external AI4Bharat assets (not in the ZIP)

1. Clone the official source checkout to a folder outside the application source tree. Example:

   ```powershell
   New-Item -ItemType Directory -Force C:\AIAssets | Out-Null
   git clone https://github.com/AI4Bharat/INCLUDE.git C:\AIAssets\INCLUDE
   ```

2. The official `pretrained_links.json` index currently lists the checkpoint key `include50_no_cnn_transformer_small.pth` with the download target below. Download it to an explicit external asset folder and name the output file using the checkpoint key expected by the project:

   ```powershell
   New-Item -ItemType Directory -Force C:\AIAssets\checkpoints | Out-Null
   Invoke-WebRequest `
     -Uri 'https://api.wandb.ai/files/abdur-ai4bharat/include50-no-cnn/11d20bb9/augs_transformer.pth' `
     -OutFile 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth'
   Get-Item 'C:\AIAssets\checkpoints\include50_no_cnn_transformer_small.pth' | Select-Object FullName,Length
   ```

   The URL is the one listed by the official repository checkpoint index; this project build did not download, hash-verify or load it. If it fails, open the official index at [`pretrained_links.json`](https://github.com/AI4Bharat/INCLUDE/blob/master/pretrained_links.json) and follow the current entry rather than using a third-party mirror.

3. Configure the external paths in `.env` (use forward slashes or quote Windows paths):

   ```dotenv
   AI4BHARAT_REPO=C:/AIAssets/INCLUDE
   ISL_CHECKPOINT=C:/AIAssets/checkpoints/include50_no_cnn_transformer_small.pth
   ISL_LABEL_MAP=
   ISL_HOST=127.0.0.1
   ISL_PORT=8000
   ISL_CONFIDENCE_THRESHOLD=0.40
   ISL_IOT_MIN_CONFIDENCE=0.70
   ISL_IOT_ENABLED=false
   ```

   Blank `ISL_LABEL_MAP` uses the bundled official 50-class map. The checkpoint remains outside the source tree and is excluded from Git/ZIP.

4. Run the asset-aware test and preflight:

   ```powershell
   python .\scripts\preflight.py
   python -m pytest -q
   ```

   `test_official_checkpoint_loads_strictly` will run (instead of skip) when both asset paths exist. A successful strict load is still not enough: reproduce the archived `court` probe and verify actual output shape/index/confidence in this environment before claiming model integration is ready.

## 4. Start the web application

```powershell
.\scripts\start_app.ps1
```

or explicitly:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn isl_recognition.webapp:app --app-dir .\src --host 127.0.0.1 --port 8000 --ws-max-size 4000000
```

Open <http://127.0.0.1:8000>. Check `http://127.0.0.1:8000/api/health` for `model_ready`, `extractor_ready`, error text, class count and configuration. A degraded health status is expected until the real repository/checkpoint and MediaPipe are configured. No fake model fallback is used. Allow camera access only for the local page.

### Capture flow

1. Ensure `/api/health` reports model and extractor ready.
2. Click **Enable camera**, allow browser permission, and verify the preview.
3. Keep the full upper body/hands visible and click **Start capture**.
4. Perform one isolated sign, then click **Stop / recognise**. Or stop after reaching the 169 extracted-frame cap.
5. Review the latest prediction, confidence, status, elapsed time, frame counts and history. Invalid capture or a confidence below the configured recognition threshold is rejected. The current `.40` text threshold is provisional and uncalibrated; IoT has a separate `.70` provisional gate.
6. Recognition history persists in SQLite. Raw frames are not written to disk by default.

## 5. Run the MQTT simulator (no physical hardware)

Keep three PowerShell terminals open.

**Terminal A — local broker** (Mosquitto must already be installed; run from project root):

```powershell
mosquitto -c .\broker\mosquitto-local.conf -v
```

**Terminal B — simulated ESP32 subscriber:**

```powershell
.\.venv\Scripts\Activate.ps1
python .\scripts\run_mqtt_simulator.py
```

Expected connection message names `isl/command/v1`. The simulator logs `SIMULATED LED BLINK` only for a valid, unexpired, allowlisted `LED_BLINK` command; it does not touch a GPIO.

**Terminal C — start/use the app:**

```powershell
.\scripts\start_app.ps1
```

The simulator will refuse to start until `config/iot_label_map.json` contains exactly two selected official labels and their approved commands. The current pre-IoT build is hard-locked, so the UI/API must not enable hardware actions. A later IoT release must configure the same two label/command pairs in the ESP32 firmware compile-time macros. Recognition confidence and the separate `.70` provisional IoT minimum must both pass; cooldown and one-command-per-capture also apply. Inspect the separate IoT history for `pending`, `acknowledged`, `rejected`, `timed_out` or `publish_failed`. Broker publish success is not proof of device execution. The broker example uses anonymous access on loopback only and must not be used as-is for LAN hardware.

If Mosquitto or Paho is unavailable, recognition should still work but the IoT attempt is recorded as failed/unconfirmed. Install Paho using `python -m pip install -r requirements-mqtt.txt`. The simulator script exits with a clear error when that package is absent.

## 6. Automated tests and diagnostics

```powershell
python .\scripts\preflight.py
python -m pytest -q
python -m compileall -q src scripts tests
```

The official-checkpoint test is skipped with a reason if external assets are absent. The current repo test suite uses explicit test doubles for capture/inference integration; those results do not claim the real model was loaded. PowerShell runner alternative:

```powershell
.\scripts\run_tests.ps1
```

## 7. Dataset evaluation (real files only)

Prepare a CSV with columns `video_path,label` and optional `split`; labels must match the official label map. Resolve paths relative to `--data-root`:

```powershell
python .\scripts\prepare_official_validation_manifest.py --repo C:\AIAssets\INCLUDE
python .\scripts\download_include_reference_clips.py --profile validation-all --ai4bharat-repo C:\AIAssets\INCLUDE --destination C:\AIAssets\INCLUDE_videos --download
python .\scripts\evaluate_dataset.py --manifest .\outputs\include50_official_val_manifest.csv --data-root C:\AIAssets\INCLUDE_videos --split val --out-dir .\outputs\include50_validation_baseline
```

For a limited smoke run, add `--max-samples 10`; its output is explicitly incomplete. The `test` split is blocked unless the one-time final frozen evaluation is intentionally unlocked with `--allow-test-evaluation`. The report distinguishes all manifest rows, selected split rows, run-selected rows, accessible files, attempted, successful, failed and missing items. Metrics are only computed on successful inferences. Verify checkpoint provenance and split independence before claiming benchmark quality. If the model or MediaPipe assets are unavailable the script exits with a blocked message instead of fabricating metrics.

## 8. Runtime/system benchmarks

`benchmark_runtime.py` measures the configured model against real preprocessed `.npy` samples only; input shape should be `(N,169,134)` or `(169,134)`. It reports warmup and measurement counts. Example:

```powershell
python .\scripts\benchmark_runtime.py --features-npy .\outputs\model_inputs.npy --warmup 5 --samples 50 --out .\outputs\model_benchmark.json
python .\scripts\benchmark_system.py --db-path .\var\isl_events.sqlite3 --out .\outputs\system_benchmark.json
```

System metrics derive from actual recorded app sessions; a missing value means it was not measured. These tools do not synthesize timings.

## 9. Local history reset and retention

The UI's refresh/history views do not trigger commands. The API requires explicit confirmation for reset/retention. To reset all logs manually after app/testing, use the UI's confirmed reset feature or send `POST /api/history/reset` with `{"confirm":true}`. For retention, send an ISO-8601 timezone-aware timestamp in `before` plus `"confirm":true` to `POST /api/history/retention`. Backup the SQLite database first if logs matter.

## 10. Hardware is a separate gate

Physical integration is **not** tested. Before connecting anything, identify the exact ESP32 board, confirm GPIO electrical limits, LED polarity and resistor, board power and wiring, compile the sketch with the correct libraries, and first validate the protocol with a simulator. Use only one low-voltage LED initially. No relay, mains appliance or high-power output is in scope.
