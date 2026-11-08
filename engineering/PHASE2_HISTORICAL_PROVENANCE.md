# Historical INCLUDE Feature-Generator Provenance Audit

## Finding

The pinned AI4Bharat repository identifies the feature-generation API and preprocessing contract, but it does **not** identify the exact MediaPipe package version/build used when the checkpoint's training keypoints were created. The upstream requirements file has no version pin or environment lock. Historical detector parity therefore cannot be claimed from the repository alone.

## Verified facts from the pinned upstream revision

Repository: `AI4Bharat/INCLUDE`  
Commit: `df348f4a640060d4d39f02556d9f96851e8c46c7`

| Source file | Verified behavior |
|---|---|
| `generate_keypoints.py` | Creates MediaPipe Hands and Pose; confidence 0.5; Pose passes `upper_body_only=True`; decodes with OpenCV and converts BGR→RGB; preserves 25 pose points and two 21-point hand slots; uses wrist indices 15/16 to place a single detected hand. |
| `dataset.py` | Linear interpolation with both-direction fill; all-missing coordinate arrays become zero; x scaled by 1920, y by 1080; concatenate 50 pose + 42 hand1 + 42 hand2 features; pad to 169 in the training call. |
| `train_nn.py` | The INCLUDE-50 keypoint dataset is configured with `max_frame_len=169` for train/validation/test dataset construction. |
| `configs.py` | Transformer-small uses 134 input features, hidden size 256, 4 attention heads and 2 layers. |
| `models/transformer.py` | Transformer uses the expected model structure; current checkout has only the approved inference dropout call fix. |
| `requirements.txt` | Lists `mediapipe` without a version pin; no lockfile is provided. |

Upstream source references:

- [generate_keypoints.py at the pinned commit](https://github.com/AI4Bharat/INCLUDE/blob/df348f4a640060d4d39f02556d9f96851e8c46c7/generate_keypoints.py)
- [dataset.py at the pinned commit](https://github.com/AI4Bharat/INCLUDE/blob/df348f4a640060d4d39f02556d9f96851e8c46c7/dataset.py)
- [train_nn.py at the pinned commit](https://github.com/AI4Bharat/INCLUDE/blob/df348f4a640060d4d39f02556d9f96851e8c46c7/train_nn.py)
- [configs.py at the pinned commit](https://github.com/AI4Bharat/INCLUDE/blob/df348f4a640060d4d39f02556d9f96851e8c46c7/configs.py)
- [requirements.txt at the pinned commit](https://github.com/AI4Bharat/INCLUDE/blob/df348f4a640060d4d39f02556d9f96851e8c46c7/requirements.txt)

## Current runtime is not the historical runtime

The target Windows environment used MediaPipe 0.10.21. The application uses the modern full-body Pose model and selects the first 25 upper-body indices, preserving the expected slot count/order. The deprecated historical `upper_body_only=True` path is handled by a diagnostic compatibility shim that selects the first 25 outputs of modern full Pose. That shim is a shape/index adapter only; it does not recreate the old detector/model weights or coordinate distribution.

The current application hand slot behavior was compared with upstream source:

- when one hand is detected, both use pose wrist points 15 and 16 to decide left/right slot;
- when two hands are detected, both retain MediaPipe detector order;
- no blanket two-hand ordering correction is supported by the inspected upstream implementation.

## Historical wheel/environment decision

The handover's prerequisite audit records no verified MediaPipe 0.8.3.1 CPython 3.9 Windows x64 wheel and no Python 3.9 interpreter on the target machine. The candidate version was not established as the actual training-time version. Do not install an arbitrary legacy wheel or alter the active .venv.

A historical-parity attempt is allowed only if an authoritative training environment file, lockfile, build log, reproducible source artifact or verified original wheel/hash is recovered. It must run in a separate isolated environment, never in the current application venv.

## Decision

`historical_extractor_parity = UNPROVEN`.

The all-77 Phase 2 runner quantifies the current application against the pinned upstream feature-generation code using the same modern MediaPipe runtime, but it does not convert that into historical detector parity. If authoritative historical artifacts remain unavailable, document this as an irreducible reproducibility limitation and continue validating the current pipeline without changing the locked model contract.
