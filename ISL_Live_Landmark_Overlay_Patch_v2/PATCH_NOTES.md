# ISL Live Landmark Overlay Patch v2

## Why v2 exists

The prior installer expected an older WebSocket close predicate. The installed version reported by the user already uses:

`const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');`

v2 validates and preserves that predicate rather than replacing it.

## Behaviour

- Keeps the existing local landmark-preview WebSocket alive while capture state is `capturing`.
- Samples the separate diagnostic preview at approximately 2 updates per second during recording.
- Retains the existing 480-pixel diagnostic frame width and JPEG quality 0.68 to limit CPU cost.
- Closes and clears the diagnostic overlay when full-clip extraction begins (`processing`).
- Restarts the diagnostic preview when capture returns to `ready`.
- Guards message/error/close callbacks from stale WebSocket instances after a reconnect.

## Scope and safety

Only `src/isl_recognition/static/landmark_diagnostics.js` is modified. The installer creates a timestamped backup and installs one test file.

Not modified: `app.js`, HTML, CSS, backend Python, capture WebSocket, recorded frames, feature extraction, 134-D features, classifier, model/checkpoint, confidence threshold, or IoT controls.

The overlay uses a separate local MediaPipe preview WebSocket, so it does add CPU work while recording. Sampling is throttled to about 2 Hz to reduce this impact. Compare captured-frame counts and dropped frames with the overlay on and off. If frame drops increase, turn the overlay off during capture; recognition input remains separate from the diagnostic preview.

## Install

Run `APPLY_ISL_LIVE_LANDMARK_OVERLAY_PATCH.ps1` from the project root after extracting the zip. It runs the overlay tests and full project test suite.
