# ISL live landmark overlay during recording

## Purpose
Keep the existing display-only local MediaPipe pose/hand overlay visible during the actual recording window.

## Behaviour
- Existing pose skeleton and hand/finger landmarks remain drawn on the transparent preview canvas.
- During recording, the separate `/ws/landmark-preview` session stays active but samples at a maximum scheduled cadence of about 2.8 updates/second.
- During recording, diagnostic frames are resized to at most 640 px wide and JPEG quality 0.72, matching the main capture stream's 640 px analysis width without changing the stream that is sent to the classifier.
- During full-clip landmark extraction/inference, the visual preview session is stopped to avoid competing with the production recognition extractor; it restarts when the app returns to ready.
- Existing arm/ready logic still requires pose in at least 4/5 recent samples and at least one hand in at least 4/5 recent samples.

## Explicitly unchanged
`app.js`, HTML, CSS, backend Python, classifier, checkpoint, label map, 134-D features, feature preprocessing, confidence threshold, acceptance policy, stored capture frames, and IoT gate are not modified.

## Verification
The installer creates a timestamped backup of the existing `landmark_diagnostics.js`, installs a focused regression test, optionally checks JavaScript syntax with Node.js, runs the targeted test, and then runs the complete `tests` suite.

## Limitation
Landmark previews are sampled guidance, not frame-by-frame tracking at the camera's capture FPS. Reduced-rate sampling is intentional to protect the capture path.
