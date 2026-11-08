# Local Reference Gallery Benchmark

## Purpose

This script runs a batch of locally available INCLUDE-50 videos through the project's configured MediaPipe extractor, preprocessing, and checkpoint. The checkpoint is loaded once and reused across clips. It reports top-1/top-5 accuracy on the selected local clip set, per-clip landmark coverage and extraction latency, and an exploratory confidence-threshold sweep.

This is a **smoke benchmark**, not an official test-set evaluation. If there is one clip per class, class accuracy estimates are extremely noisy. It cannot establish performance on a new webcam signer or calibrate a production threshold by itself.

## Folder structure

The expected label is inferred from a class folder matching a canonical INCLUDE-50 label. Both `Greetings/48. Hello/MVI_0032.MOV` and similar numbered class folders work.

## Command

From the project root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

.\.venv\Scripts\python.exe .\scripts\benchmark_reference_gallery.py `
  --video-root "C:\AIAssets\INCLUDE_videos" `
  --resize-width 640 `
  --samples-per-class 1 `
  --output reference_gallery_benchmark_640.json
```

This defaults to 640px to approximate the current web capture diagnostic width. Run a separate native-resolution diagnostic only for comparison, not as a direct replacement for the operational-path benchmark:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark_reference_gallery.py `
  --video-root "C:\AIAssets\INCLUDE_videos" `
  --resize-width 0 `
  --samples-per-class 1 `
  --output reference_gallery_benchmark_native.json
```

The program prints progress and writes full JSON. Model load happens once; feature extraction remains the slow part on CPU.

## Interpretation

- `top1_accuracy` and `top5_accuracy`: correctness on these particular local clips only.
- `threshold_sweep_exploratory_only`: accepted coverage and selective accuracy at candidate thresholds; do not select the production threshold based only on this small set.
- Per-clip coverage and extractor time: help identify videos that are difficult for the current detector.
- Compare 640 and native reports to quantify input resizing effects.

Keep IoT disabled. Do not modify model weights, feature dimension, 169-frame sequence contract, or threshold as part of this diagnostic.
