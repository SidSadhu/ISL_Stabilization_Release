# Phase 6 PowerShell stderr correction and saved-capture recovery

The Phase 6 browser step completed and saved six RVFC capture containers. The original launcher can still stop at the Python analyzer because Windows PowerShell with `$ErrorActionPreference = 'Stop'` promotes benign TensorFlow Lite/MediaPipe native stderr into terminating `NativeCommandError` records.

## Files

- `scripts/run_phase6_rvfc_alignment.ps1`: corrected main launcher. It temporarily sets `$ErrorActionPreference = 'Continue'` only around the native Python analysis process, captures all output in the analysis log, and trusts `$LASTEXITCODE` for actual success/failure.
- `scripts/complete_phase6_from_saved_capture.ps1`: recovery runner for the current attempt. It reuses the newest `browser_canvas_validation_phase6_rvfc_*` directory containing `capture_index.json` and exactly six `.islbrw` files; no browser recapture and no test rerun are required. It preserves the previous full pytest log, reruns the analyzer, and creates a checksum-verified result bundle.

## Recovery

Extract this patch over the existing project root. Then run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& '.\scripts\complete_phase6_from_saved_capture.ps1' `
  -RepoPath 'C:\AIAssets\INCLUDE' `
  -DataRoot 'C:\AIAssets\INCLUDE_videos' `
  -Manifest '.\outputs\include50_official_val_manifest.csv' `
  -Phase5Dir '.\outputs\phase5_canvas_pixel_audit'
```

The recovery runner verifies the existing six captures, manifest presence, Python 3.12, empty IoT map and pre-IoT hard lock. It does not modify application files or settings.

Expected output: `outputs/PHASE6_RESULT_BUNDLE_*.zip`.
