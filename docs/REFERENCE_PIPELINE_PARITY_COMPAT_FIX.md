# Reference pipeline parity diagnostic — MediaPipe compatibility fix

## Why the previous run failed

The installed environment reports MediaPipe 0.10.21. The checked-out AI4Bharat `generate_keypoints.py` calls `mp.solutions.pose.Pose(..., upper_body_only=True)`, but that argument is not available in the current Python Solutions Pose API. As a result, the parity test failed before the official generator could finish, and the previous run did **not** produce a comparison report.

## What changed

The diagnostic now detects whether the installed `Pose` constructor supports `upper_body_only`.

- If supported, it uses the original option as requested by the repository code.
- If unsupported, it applies a temporary adapter only inside the diagnostic process: it runs current full-pose inference and returns only the first 25 landmarks when the legacy generator requests upper-body output. The rest of the generator, including hand extraction, wrist-based hand-slot assignment, and JSON generation, runs unchanged.

## Important scientific limitation

The compatibility fallback is **shape-compatible, not exact historical parity**. An older MediaPipe upper-body-only mode could use a legacy model/configuration distinct from simply slicing 25 points from the current full-body model. Therefore, the report will say `compat_full_pose_first_25_points_not_exact_legacy_model`; feature differences are useful diagnostic evidence but must not be described as proof of exact parity with the original training pipeline.

Do not downgrade the working application environment based on this error alone. To reproduce the exact historical extractor, we would need to identify and recreate the MediaPipe version/model used to generate the training keypoints, which may not be compatible with Python 3.12.

## Run

From the project root, run both comparisons:

```powershell
$ref = 'C:\AIAssets\INCLUDE_videos\Greetings\48. Hello\MVI_0032.MOV'

.\.venv\Scripts\python.exe .\scripts\diagnose_reference_parity.py `
    "$ref" --expected hello --resize-width 0 --repeat 5 `
    --output reference_parity_native.json

.\.venv\Scripts\python.exe .\scripts\diagnose_reference_parity.py `
    "$ref" --expected hello --resize-width 640 --repeat 5 `
    --output reference_parity_640.json
```

The report also checks whether identical input tensors produce changing predictions. The AI4Bharat Transformer source contains `F.dropout(x, p=0.2)` without a `training=self.training` argument, which PyTorch's functional API defaults to training mode. Therefore, repeated predictions can be stochastic even after `model.eval()`. This diagnostic detects that behavior but does not edit upstream model code.

This is a single-video pipeline diagnostic, not an accuracy evaluation. Keep IoT disabled and do not calibrate the confidence threshold from one clip.
