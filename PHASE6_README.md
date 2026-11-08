# Phase 6 — requestVideoFrameCallback and PTS alignment

## Purpose

Phase 5 found that the best pixel match between the saved timer-based browser Canvas frames and OpenCV source frames was usually one frame earlier. That result was not conclusive because Phase 5 sampled with `video.currentTime` and compared against nominal-FPS frame indices.

Phase 6 captures the same six diagnostic clips with `HTMLVideoElement.requestVideoFrameCallback`. The JPEG is drawn synchronously inside that callback and uses `metadata.mediaTime` as its frame timestamp. The capture also records `video.currentTime`, `metadata.presentedFrames`, expected display time, callback time, processing duration, and Canvas encoding duration.

The offline audit then matches the callback's media time against source presentation timestamps (`ffprobe` best-effort PTS where available; otherwise sequential OpenCV timestamps, with a clearly labelled nominal-FPS fallback only if necessary). It runs a bounded pixel search at offsets -1/0/+1 around that PTS-selected frame and performs a static-image MediaPipe Hands replay.

## Safety and scope

- Six diagnostic validation clips only; the 77-item official validation manifest and pinned repository are still verified before capture.
- Captures are written to a new timestamped folder and do not overwrite the Phase 1/2/3/4/5 browser evidence.
- The capture server binds only to `127.0.0.1` and exposes only manifest, status and capture upload routes.
- IoT must remain disabled and the label-command map must remain empty.
- Classifier/checkpoint is not imported or loaded by the analyzer.
- No production feature extraction, slot assignment, model input, checkpoint, vocabulary, confidence threshold, or app setting is changed.

## Files

- `scripts/browser_validation_capture_rvfc.html` — six-clip browser capture page.
- `scripts/serve_phase6_rvfc_capture.py` — loopback capture server; persists the RVFC provenance in `capture_index.json`.
- `scripts/phase6_rvfc_alignment_diagnostics.py` — source PTS alignment, pixel metrics and static MediaPipe analysis.
- `scripts/run_phase6_rvfc_alignment.ps1` — one-command test, browser capture, analysis, and result-bundle workflow.
- `tests/test_phase6_rvfc_alignment.py` — focused regression tests.

## Run

Extract the patch ZIP into the existing project root, then execute:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& '.\scripts\run_phase6_rvfc_alignment.ps1' `
  -RepoPath 'C:\AIAssets\INCLUDE' `
  -DataRoot 'C:\AIAssets\INCLUDE_videos' `
  -Manifest '.\outputs\include50_official_val_manifest.csv' `
  -Phase5Dir '.\outputs\phase5_canvas_pixel_audit'
```

The local browser page opens. Select `C:\AIAssets\INCLUDE_videos`, confirm all six target clips resolve, and click **Capture six clips with frame callbacks**. Keep the PowerShell session open; it waits for the six saves and then performs analysis and bundle creation.

`ffprobe` is optional. If it is installed and its frame count agrees with OpenCV, Phase 6 uses its `best_effort_timestamp_time`; otherwise the report records the fallback timestamp source. Do not interpret an OpenCV nominal-FPS fallback as definitive PTS validation.

## Expected deliverable

`outputs/PHASE6_RESULT_BUNDLE_YYYYMMDD_HHMMSS.zip`, containing the report, timing/pixel/detector CSVs, summary JSON, exact six RVFC capture containers, full test log, capture-server logs, environment metadata and SHA-256 manifest.
