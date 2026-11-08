# Phase 3 PowerShell stderr hotfix

This hotfix updates only `scripts/run_phase3_hand_diagnostics.ps1`.

Reason: MediaPipe/TensorFlow Lite may write an informational XNNPACK delegate message to native stderr. Windows PowerShell can surface native stderr as a `NativeCommandError`; with `$ErrorActionPreference = 'Stop'`, that may terminate the launcher before it checks Python's real exit code. The patched launcher temporarily uses `Continue` only around the Python diagnostic process and then checks `$LASTEXITCODE` as before.

No Python source, project application code, model, checkpoint, class order, feature contract, IoT config, data, or captures are modified.

Install by extracting this ZIP into the project root, overwriting the existing launcher script. Then rerun `scripts/run_phase3_hand_diagnostics.ps1` with the same arguments as before.
