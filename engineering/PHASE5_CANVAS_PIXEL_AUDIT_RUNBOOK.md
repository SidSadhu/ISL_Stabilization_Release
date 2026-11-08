# Phase 5 — Browser Canvas Pixel Audit

## Objective

Phase 4 established that the actual 640-pixel browser capture and OpenCV's 640-pixel JPEG approximation can produce materially different MediaPipe hand-detection results on the `Good Morning` outlier. Phase 5 measures the pixel-level difference before making any production recommendation.

This phase is diagnostic-only. It must not load the Transformer checkpoint, create predictions, tune thresholds, change the 134-D layout or 169-frame sequence, modify production extractor behavior, or enable IoT.

## Inputs

- Pinned official INCLUDE-50 validation manifest: 77/77 exact entries.
- Six existing Phase 3/4 diagnostic browser captures under `outputs/browser_canvas_validation`.
- Original source videos under `C:\AIAssets\INCLUDE_videos`.
- Python 3.12, OpenCV 4.11.0, MediaPipe 0.10.21.
- AI4Bharat repository at pinned commit `df348f4a640060d4d39f02556d9f96851e8c46c7`.

## Work performed

1. Decode the exact JPEG bytes stored in each `ISLBRW01` Canvas capture.
2. Reuse the timestamp-aligned source frames established by the Phase 3 alignment helper.
3. Create source candidates at offsets -1, 0 and +1 frames solely to test whether a neighboring source frame is more visually similar to the browser JPEG.
4. Resize each source candidate to the exact captured canvas dimensions using OpenCV nearest, linear, area, cubic and Lanczos4 interpolation.
5. Round-trip each resized candidate through PNG and JPEG quality 92, then calculate full-frame BGR MAE, RMSE, PSNR, channel MAE and signed channel bias against the decoded actual browser JPEG.
6. Run MediaPipe Hands with `static_image_mode=True` over the actual browser JPEGs and six controlled native resize/codec variants. This disables temporal tracking so the detector comparison is about image content rather than tracking history.

## Outputs

- `phase5_summary.json`
- `PHASE5_CANVAS_PIXEL_AUDIT_REPORT.md`
- `phase5_frame_pixel_metrics.csv` — every clip/frame/interpolation/codec/source-offset comparison.
- `phase5_pixel_transform_summary.csv` — clip-level metrics for each interpolation/codec/offset.
- `phase5_best_pixel_match_per_frame.csv` — closest pixel candidate for each browser frame.
- `phase5_static_detector_frames.csv` — per-frame MediaPipe hand counts and handedness.
- `phase5_static_detector_pairwise.csv` — detector parity against actual browser static mode.
- `phase5_clip_metadata.csv`

## Interpretation constraints

- The best source-frame offset is a pixel-similarity diagnostic only; it does not replace the timestamp-selected source frame or prove what the browser displayed.
- OpenCV resize and JPEG encoding approximate the browser pipeline but cannot claim pixel identity with the browser's Canvas/codec implementation.
- Pixel MAE/PSNR is not gesture-recognition accuracy.
- Static-image MediaPipe mode intentionally removes tracking history. Phase 4 remains the evidence for tracking effects.
- Six clips are a targeted diagnostic subset, not a class-balanced generalization study. Any proposed production change must be evaluated on the frozen official validation split before release.

## Execution

Run from PowerShell in the project directory after overlaying the Phase 5 patch:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& '.\scripts\run_phase5_canvas_pixel_audit.ps1' `
  -RepoPath 'C:\AIAssets\INCLUDE' `
  -DataRoot 'C:\AIAssets\INCLUDE_videos' `
  -Manifest '.\outputs\include50_official_val_manifest.csv' `
  -CaptureDir '.\outputs\browser_canvas_validation' `
  -OutputDir '.\outputs\phase5_canvas_pixel_audit'
```

The launcher runs the full project test suite first, captures the Python run log, checks Python's actual exit code even when MediaPipe emits native stderr messages, verifies the outputs, hashes the result files, creates a ZIP, and verifies ZIP integrity.
