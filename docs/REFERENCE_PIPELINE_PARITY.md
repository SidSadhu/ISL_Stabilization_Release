# Reference pipeline parity diagnostic

## Why it exists

The single-clip diagnostic produced `fan` as top-1 and `hello` as top-2 despite 100% pose and any-hand coverage. This does not prove the checkpoint is broken, but it means capture duration alone is not an adequate explanation. This script compares the project extractor with the official AI4Bharat `generate_keypoints.py` path on the same clip.

## Run on Windows

From `ISL_Recognition_Project`, with the app allowed to keep running or stopped (the script uses a separate extractor session):

```powershell
$ref = 'C:\AIAssets\INCLUDE_videos\Greetings\48. Hello\MVI_0032.MOV'
.\.venv\Scripts\python.exe .\scripts\diagnose_reference_parity.py "$ref" --expected hello --resize-width 0 --repeat 5 --output reference_parity_native.json
```

Then run again at the app's 640px input width to measure resolution-related differences:

```powershell
.\.venv\Scripts\python.exe .\scripts\diagnose_reference_parity.py "$ref" --expected hello --resize-width 640 --repeat 5 --output reference_parity_640.json
```

The test imports the exact local `C:\AIAssets\INCLUDE\generate_keypoints.py` helper, which uses the original upper-body pose mode and processes source frames without resizing. The application path uses the project's current extractor and configured preprocessing/checkpoint. Both predictions use the same checkpoint and a reset RNG seed for the paired comparison; repeated application predictions are then run without resetting the RNG to detect stochastic output.

## Important interpretation

- Large app-vs-official feature differences: investigate pose configuration, resize, hand slot assignment, interpolation, and coordinates before tuning capture duration or confidence.
- Similar features and matching expected prediction: the main problem shifts toward webcam framing, signing variation, capture window, or domain shift.
- Same exact app tensor gives changing predictions: inspect `models/transformer.py` for an unconditional `F.dropout(x, p=0.2)` call.
- The upstream AI4Bharat model source currently contains `F.dropout(x, p=0.2)` without a `training=self.training` argument. Since functional dropout defaults to `training=True`, `model.eval()` alone does not disable that operation. A conventional inference-mode correction is `F.dropout(x, p=0.2, training=self.training)`. Do not patch the shared repository silently: record a backup and compare deterministic outputs as part of the experiment.

This is a pipeline diagnostic on one clip, not an accuracy benchmark. Do not choose the confidence threshold from a single example. Keep IoT disabled.
