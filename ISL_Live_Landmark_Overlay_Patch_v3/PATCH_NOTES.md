# ISL Live Landmark Overlay Patch v3 — reconnect recovery

## Why this patch exists

The v2 close predicate considered a normal WebSocket close expected whenever the UI was not in `ready`. That could silently hide a normal preview-socket closure during `capturing`. The v2 socket-identity guard already suppresses callbacks from a socket intentionally closed by the client during processing or when the overlay is turned off.

## Behaviour

- Treat a normal close as expected only if the user turned the overlay off.
- When an active preview socket closes unexpectedly, show the disconnection and retry at 500 ms, 1 s, and 2 s (maximum three attempts).
- Do not reconnect while full-clip extraction (`processing`) is active.
- Reset retry attempts when the overlay is enabled again or capture returns to `ready`.
- Preserve existing v2 capture sampling at approximately 2 updates/second, 480 px JPEG 0.68, pose/hand drawing, and stale-socket guards.

## Scope

Only `src/isl_recognition/static/landmark_diagnostics.js` is modified. One regression test is added.

Not modified: `app.js`, HTML, CSS, Python backend, capture WebSocket, recorded JPEGs, 134-D feature extraction, model/checkpoint, confidence threshold, or IoT controls.

The overlay is still a second MediaPipe preview session and adds CPU load. If the preview disconnects repeatedly, the UI reports that fact rather than silently hiding it.

The installer also updates the v2 overlay regression test so its expected-close assertion matches v3 behaviour; the old test is backed up.
