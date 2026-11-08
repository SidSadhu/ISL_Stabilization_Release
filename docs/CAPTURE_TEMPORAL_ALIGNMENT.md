> **Historical diagnostic note:** this document describes the earlier per-frame WebSocket/queue implementation. The current primary web MVP buffers JPEG frames during manual Start/Stop capture and extracts them sequentially after Stop. Do not use the old queue-performance analysis as a description of the active webapp. See `IMPLEMENTATION_PLAN.md` and `docs/HANDOFF.md`.

# Capture temporal alignment (2026-10-29)

## Why the capture flow changed

The official INCLUDE evaluation uses a maximum sequence length of 169 frames and pads shorter sequences to this length; 169 is not a minimum and not a duration in seconds. The application allows up to 6 seconds of capture with an additional 2-second pre-capture countdown. With an approximately 25 FPS webcam, 6 seconds is roughly 150 frames, under the 169-frame cap. The full captured clip is still extracted sequentially after recording, preserving the model input contract.

The previous live WebSocket path ran MediaPipe on each frame as it arrived. On machines where landmark extraction is slower than the camera send rate, the small queue dropped older frames; a long capture could collect 169 extracted features over many seconds, distorting the temporal pattern.

## New behavior

- Captures source JPEG frames in memory at the browser's nominal 25 FPS send cadence.
- Stops after the user clicks Stop or after `ISL_MAX_CAPTURE_SECONDS` (default 6.0s). A 2-second get-ready countdown happens before the recorded clip begins; stop as soon as one complete sign is finished rather than intentionally filling the full window.
- Performs MediaPipe landmark extraction sequentially after recording, then runs the existing 134-D preprocessing and one model inference on the unchanged `(169, 134)` padded tensor.
- Does not write raw camera frames to disk. The tradeoff is that landmark extraction and inference happen after capture and can take additional seconds.
- Keeps 169 as the hard maximum frame count to preserve the pretrained model contract.

## Interpreting the UI

`Captured frames` are source images received from the browser. `Extracted features` are frames for which landmark extraction succeeded. The capture duration reports the recorded clip window, not the processing duration. `Processing latency` includes post-capture landmark extraction, preprocessing and inference; `Inference latency` is model-forward time only.

## Reference clip coverage

The gallery intentionally retains all 50 INCLUDE-50 classes, but the selective demo download only provides local clips for a subset (the 25-class demo, plus earlier leftover examples). A count such as `30 clips ready` is the number of local clip records, not 30 of 50 classes. The gallery should show both the count of classes with a local clip and total local clips.

This change improves temporal consistency but does not guarantee recognition accuracy. Model evaluation on held-out signer/session data and calibration of the confidence threshold remain separate work.
