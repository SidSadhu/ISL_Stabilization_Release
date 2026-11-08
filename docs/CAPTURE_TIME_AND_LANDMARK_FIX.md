> **Historical diagnostic snapshot:** this report records earlier capture/runtime settings and a `.70` text threshold. The current web text threshold is `.40` (uncalibrated), the separate provisional IoT gate is `.70`, and the main web path now buffers JPEG frames then extracts landmarks after Stop. See `IMPLEMENTATION_PLAN.md` and `docs/IMPLEMENTATION_STATUS.md` for current behaviour.

# Capture time and live landmark responsiveness fix

## Findings from the local diagnostic report

The user's latest backend history showed 98–99 captured frames over about 4.0 seconds, ~24.4–24.7 captured FPS, and zero dropped frames. The recent model inference was ~18 ms; feature extraction took ~11.6–15.7 seconds for ~99 frames. The primary bottleneck is therefore per-frame Python MediaPipe extraction, not capture or Transformer inference. In one invalid capture, any-hand coverage was 28%; another prediction (cellphone, about 18–19% confidence) was rejected by the uncalibrated 0.70 threshold. That is evidence to keep investigating preprocessing and gesture visibility, not to lower confidence blindly.

The existing preview had 650 ms idle / 1100 ms capture delay plus processing time. It also applied old-frame coordinates to the continually moving camera image, so the overlay was visibly stale. The client now asks for the next frame shortly after each response; the server's one-request-at-a-time WebSocket design prevents an unbounded queue, and the minimum server safety interval was reduced from 180 ms to 50 ms. Preview image width is reduced to 480 px to lower guidance-only processing cost; the classifier capture remains 640 px. Result-rate calculations use response-arrival times and reject invalid intervals.

## Duration and 169 frames

The app now allows up to 6 seconds plus a 2-second get-ready countdown before recording starts. The countdown is not part of the captured clip. The maximum remains 169 source frames and classifier input remains exactly (169, 134): shorter sequences are padded, longer sequences are capped according to app behavior. With ~25 FPS, six seconds typically contains about 150 frames and fits under this cap. The user should stop after one complete isolated sign rather than filling all six seconds.

This does not claim six seconds is required by the pretrained model. A 169-frame model sequence length is not a 169-frame minimum. Duration may help leave enough time to complete the gesture, but it will not solve hand visibility, wrong feature ordering, class-map mismatch, or domain differences.

## Next experiment

Run `scripts/diagnose_reference_video.py` against a local INCLUDE reference clip using the configured checkpoint. This takes the clip through the actual extractor, preprocessing and model. If the official reference is top-1 expected with reasonable confidence and good coverage, investigate camera framing/sign timing and webcam domain next. If the official reference is not classified correctly, inspect label order, feature ordering, hand slot assignment, coordinates, interpolation and checkpoint/repo consistency before further UI or threshold tuning. One clip is a smoke test, not a benchmark.

Keep confidence threshold 0.70 and hand-coverage minimum 0.50 provisional. Keep IoT disabled. Do not skip frames from the classifier pipeline until comparative evaluation demonstrates acceptable performance.

## Sources

- AI4Bharat INCLUDE source repository and model/preprocessing pipeline: https://github.com/AI4Bharat/INCLUDE
- Official MediaPipe Hands documentation explains stream tracking when `static_image_mode=false`, which reduces latency for video: https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/hands.md
- Official MediaPipe Pose documentation explains tracking between frames and complexity/latency trade-offs: https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/pose.md
- MediaPipe Python Hand Landmarker docs describe VIDEO/LIVE_STREAM modes and asynchronous callbacks; a busy live-stream detector can drop submitted frames, so a single-flight preview is appropriate: https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python
