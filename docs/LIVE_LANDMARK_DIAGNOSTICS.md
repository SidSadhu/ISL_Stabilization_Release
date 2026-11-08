# Live Landmark Diagnostics and Capture Validation

## What was added

- A live overlay for hand skeletons and upper-body pose landmarks, rendered over the camera preview.
- The overlay now uses the project's **local Python MediaPipe Hands/Pose extractor over a local WebSocket**. It no longer depends on browser CDN/WASM/model downloads. This change was made after the UI showed `Pose: Unavailable`, `Hands: Unavailable`, and a CDN-loading warning.
- Preview frames are resized to at most 480 pixels wide and sent as transient JPEGs to `ws://127.0.0.1:<port>/ws/landmark-preview`; they are processed in memory and never stored. The preview connection uses one request at a time, sends the next request shortly after each response, and yields while the recorded clip is being processed and yields while the recorded clip is being processed.
- The backend can return native normalized pose/hand landmarks for display while keeping recognition's feature-only path free of diagnostic landmark-list allocations.
- The backend returns separate coverage for any hand, hand slot 1, hand slot 2 and both hand slots, plus total feature-extraction time, setup time, median and p95 per-frame extractor latency, and extraction throughput.
- An optional **Diagnostic override** for low-but-nonzero hand coverage. It bypasses only the provisional hand-coverage gate; minimum frame count, pose coverage and extraction-failure validation remain in force. A clip with zero detected hands is still rejected. Every capture submitted in diagnostic mode suppresses IoT commands even if IoT is enabled globally.

## Model contract intentionally unchanged

The browser overlay is a guidance-only detector. Its landmarks are never sent to the pretrained classifier. The backend still uses the project's Python MediaPipe feature extractor, fixed 134-dimensional per-frame layout, and 169-frame preprocessing contract. We did not change the checkpoint, class map, coordinate scaling, hand-slot assignment logic, padding/truncation behavior, six-second maximum (configurable within the existing 1–6 second bound), or 0.70 confidence threshold.

The existing hand-coverage threshold remains provisional. The backend currently defines `hand_ratio` as the fraction of successfully extracted feature frames in which at least one hand is detected. `hand1_ratio`, `hand2_ratio`, and `both_hand_ratio` clarify cases the former combined metric concealed. Threshold tuning still requires repeated labelled recordings.

## Local-preview network and privacy note

The live overlay communicates only with the local app's backend. It does not fetch MediaPipe JavaScript, WASM or model assets from jsDelivr or Google hosting. The local Python environment must already have the project's required OpenCV and MediaPipe dependencies installed; the runtime status and `/api/health` report whether the backend extractor is available. Diagnostic JPEGs are transient in-memory messages, not persisted to SQLite or disk by this endpoint. The recognition socket continues to collect the original captured frames and runs its own extraction pass after capture.

The browser canvas is CSS-mirrored along with the camera preview so landmark coordinates align with the displayed image. The overlay remains a guide only and does not replace the model's 134-D features or 169-frame sequence.

## Apply the patch

1. Stop the app with Ctrl+C.
2. Extract `ISL_Live_Landmark_Diagnostics_Offline_Fix.zip` directly into the existing `ISL_Recognition_Project` folder and allow the listed source files to be overwritten.
3. Do not replace `.env`, `.venv`, your model checkpoint, `C:\AIAssets\INCLUDE_videos`, or `data\include50_video_metadata.json`.
4. Start the app with the existing Windows startup script.
5. Hard-refresh the page using Ctrl+F5.
6. Click Enable camera / preview. The Pose and Hands status boxes should change from Waiting to detected/weak results, and a yellow pose / green hand skeleton should appear. Adjust framing before recording. If low backend hand coverage persists, keep hands/wrists/fingertips visible and repeat the reference sign. Only for a controlled test, tick Diagnostic override and submit a low-coverage capture. IoT remains suppressed for that attempt.

## Validation performed in the packaging environment

- Python compileall: passed.
- Python compileall and JavaScript syntax checks: passed.
- Automated tests: 43 passed, 1 skipped, including the local `/ws/landmark-preview` landmark response and the capture-validation/IoT safeguards.
- Browser hardware test not performed in this environment. The first local run must verify actual Windows webcam landmark alignment and local MediaPipe extraction latency.

## Primary implementation references

- AI4Bharat INCLUDE: https://github.com/AI4Bharat/INCLUDE
- MediaPipe Hand Landmarker for Web: https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/web_js
- MediaPipe Pose Landmarker for Web: https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/web_js
