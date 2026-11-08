# Phase 4 — Controlled Transport and Tracking Replay

## Goal

Phase 3 identified two different failure signatures: severe hand-presence divergence on Good Morning and two-hand detector-order reversal on Short. Phase 4 performs a controlled offline replay to separate image scaling/transport effects from MediaPipe temporal tracking effects.

## Seven diagnostic variants (per six selected clips)

1. `browser_canvas_tracking`: exact saved browser Canvas JPEG frames, `static_image_mode=False`.
2. `browser_canvas_static`: the same JPEG payloads, `static_image_mode=True`.
3. `native_original_tracking`: timestamp-matched original-resolution video frames, tracking mode.
4. `native_original_static`: the same original frames, static-image mode.
5. `native_resized_png_tracking`: native frames resized with OpenCV INTER_LINEAR to the exact browser capture dimensions, then PNG encoded/decoded, tracking mode.
6. `native_resized_jpeg_q92_tracking`: same controlled resize, JPEG quality 92 encoded/decoded, tracking mode.
7. `native_resized_jpeg_q92_static`: same resized JPEG payloads, static-image mode.

Each clip/variant uses a fresh Hands and Pose session. The classifier/checkpoint is not loaded. No predictions, training or threshold tuning occur. Production assignment remains `verified_one_hand_fix`; no alternate slot policy is applied. IoT stays disabled.

## Expected environment

- Windows project root with `.venv` Python 3.12.
- Existing pinned AI4Bharat repo at `C:\AIAssets\INCLUDE`.
- Existing source videos at `C:\AIAssets\INCLUDE_videos`.
- Existing official validation manifest and 77 saved browser capture containers.
- OpenCV 4.11.0 and MediaPipe 0.10.21.

## Install and run

Extract the patch ZIP into the project root, then run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& '.\scripts\run_phase4_controlled_replay.ps1' `
  -RepoPath 'C:\AIAssets\INCLUDE' `
  -DataRoot 'C:\AIAssets\INCLUDE_videos' `
  -Manifest '.\outputs\include50_official_val_manifest.csv' `
  -CaptureDir '.\outputs\browser_canvas_validation' `
  -OutputDir '.\outputs\phase4_controlled_replay'
```

The launcher runs the entire pytest suite first, then runs Phase 4 and creates a SHA-256 indexed result ZIP under `outputs`.

## Interpretation

- If native resized PNG/JPEG reproduces browser hand-presence/order behavior, resizing/transport is implicated.
- If tracking and static-image modes differ for identical image bytes, temporal tracking history is implicated.
- Compare ordered labels separately from unordered label sets; `Right|Left` vs `Left|Right` is an order reversal.
- OpenCV INTER_LINEAR resize is a controlled approximation of browser Canvas resampling, not a claim of pixel-exact Canvas equivalence.
- A result does not establish which hand-slot convention the pretrained checkpoint expects. Do not change production assignment based only on this phase.
