# Runtime behaviour

1. **Ready / arming:** Existing preview continues at its normal responsive rate and supplies the existing five-sample pose + hand readiness gate.
2. **Recording:** Existing preview WebSocket and transparent canvas remain active. The diagnostic feed is sampled at a maximum scheduled interval of 350 ms (about 2.8 Hz), resized to at most 640 px wide, and JPEG-encoded at quality 0.72. The ordinary idle preview remains 480 px / 0.68.
3. **Full-clip processing:** The display-only preview WebSocket closes to free the second MediaPipe session for the production extractor. Its close is treated as intentional, not shown as a connection failure.
4. **Ready after processing:** The preview session is restarted when the UI returns to ready. Socket-identity guards ignore stale messages or close events from the previous session.

The overlay uses the existing `poseLandmarks` and `hands` response properties to draw the pose skeleton and hand/finger connections. It does not modify camera frames, server capture payloads, or 134-D classifier features.
