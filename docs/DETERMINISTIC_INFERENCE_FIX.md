# Deterministic inference fix for AI4Bharat INCLUDE Transformer

## Evidence in the user diagnostic

- The exact same application feature tensor produced top-1 `hello` on all five runs, but confidence ranged from about 0.243 to 0.452.
- The diagnostic detects an unconditional `F.dropout(x, p=0.2)` in `C:\AIAssets\INCLUDE\models\transformer.py`.
- PyTorch's functional dropout defaults `training=True`; therefore `model.eval()` does not disable this specific call unless the model's state is passed explicitly.
- The project imports the Transformer class directly from `C:\AIAssets\INCLUDE`, so the loaded model's source implementation must be corrected and the server restarted. No checkpoint weights need to be rewritten.

## What changes

Change:

```python
x = F.dropout(x, p=0.2)
```

to:

```python
x = F.dropout(x, p=0.2, training=self.training)
```

This keeps dropout active during training and disables it during `model.eval()` inference, which is standard PyTorch behavior. This changes inference behavior, not learned weights.

## Apply on Windows

1. Stop Uvicorn with `Ctrl+C`.
2. Extract this ZIP into the existing `ISL_Recognition_Project` folder, preserving the `scripts` and `docs` subfolders.
3. From PowerShell at the project root, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\fix_include_inference_dropout.ps1
```

4. Restart the app with ` .\scripts\start_app.ps1 `.
5. Rerun the native-resolution parity test:

```powershell
$ref = 'C:\AIAssets\INCLUDE_videos\Greetings\48. Hello\MVI_0032.MOV'
.\.venv\Scripts\python.exe .\scripts\diagnose_reference_parity.py "$ref" --expected hello --resize-width 0 --repeat 5 --output reference_parity_native_after_dropout_fix.json
```

Expected result: five repeated predictions on the same tensor should have matching confidence values (within normal numerical tolerance) and top labels. This tests deterministic inference, not overall recognition accuracy.

## Roll back

Stop the app and run:

```powershell
.\scripts\restore_include_inference_dropout.ps1
```

The fixer creates `C:\AIAssets\INCLUDE\models\transformer.py.pre_isl_deterministic_inference.bak` before changing source. It is idempotent and refuses to edit unless it finds exactly one expected unconditional call.

## Interpretation of current parity results

- At native resolution, paired predictions using the same RNG seed put `hello` first in both paths at about 0.336 confidence. This is evidence of similar prediction behavior for this clip, not exact feature parity. The compatibility path slices the first 25 landmarks from the current full-pose result and is not an exact reproduction of historical `upper_body_only=True`.
- At 640-pixel width, feature differences are much larger. Do not resize reference videos for parity comparisons against the source-resolution generator. Webcam capture is a separate condition; assess that independently.
- The current web app recognition threshold is `0.40` and remains uncalibrated; `0.70` is now the separate provisional IoT command gate. Deterministic inference may stabilize the scores but does not tell us what threshold is appropriate. Do not lower it based on one clip; evaluate a labelled held-out set first.
- Keep the 134-D / 169-frame contract, six-second capture maximum, and hand-coverage rule unchanged during this experiment. This patch only addresses inference stochasticity.
