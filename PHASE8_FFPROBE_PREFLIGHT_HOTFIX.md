# Phase 8 FFprobe preflight hotfix

This overlay fixes the Phase 8 launcher preflight only. Windows PowerShell 5.1 can surface native stderr from `ffprobe -version` as `NativeCommandError` when `$ErrorActionPreference` is `Stop`, causing a false failure even though FFprobe is installed and works.

The launcher now starts FFprobe using `Start-Process`, redirects stdout and stderr to temporary files, waits for completion, checks the actual process exit code, and verifies that the output contains an `ffprobe version` line. Temporary probe files are removed afterward. No project configuration, capture code, recognition code, model, checkpoint, or IoT setting is changed.

## Apply

Extract this ZIP over the project root, allowing the two updated files to overlay: `scripts/run_phase8_callback_lifecycle.ps1` and `tests/test_phase8_callback_lifecycle.py`. A new file `PHASE8_FFPROBE_PREFLIGHT_HOTFIX.md` documents the change. Then rerun the Phase 8 launcher with the same arguments. The prior attempt stopped before tests or capture passes, so no capture data needs recovery.
