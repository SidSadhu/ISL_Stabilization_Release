ISL Live Landmark Overlay v3 - Final Capture-Gating/Test Fix

This patch is intended for a project where reconnect recovery v3 has already been installed.

What it checks/fixes:
- The capture preview scheduler and frame sender must allow busyState='capturing'.
- Both functions must pause only while busyState='processing'.
- If an earlier installed build uses `busyState !== 'ready'` in either of those two functions, it is replaced with `busyState === 'processing'`.
- Rewrites the reconnect regression test so the close predicate is inspected only inside the WebSocket close handler. It does not ban legitimate capture-state guards elsewhere in the JavaScript file.
- Runs JavaScript syntax validation (when Node.js is available), targeted overlay tests, and the full project test suite.

It does not change app.js, capture frame sender, backend Python, model/checkpoint, 134-D features, threshold, or IoT code.
A timestamped backup is created before any file is changed.
