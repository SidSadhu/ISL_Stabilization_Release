# Phase 8 — Callback lifecycle and factor isolation

## Goal

Explain why the Phase 7 no-gate + asynchronous-`toBlob` pass produced long callback blackouts and lower source-frame coverage on some clips. Separate the 39 ms callback gate from the encoder choice with a 2×2 experiment.

## Four variants

1. `gate_sync`: 39 ms minimum accepted-callback interval + synchronous `toDataURL`.
2. `ungate_sync`: no interval gate + synchronous `toDataURL`.
3. `gate_async`: 39 ms minimum accepted-callback interval + asynchronous `toBlob`.
4. `ungate_async`: no interval gate + asynchronous `toBlob`.

All four use `requestVideoFrameCallback`, synchronous `drawImage` in the callback, a 640 px Canvas, JPEG quality 0.92, the same six source videos, and FFprobe source PTS.

## Instrumentation

- `requestVideoFrameCallback` request count, delivery count, callback lateness, `metadata.presentedFrames`, `metadata.mediaTime`, and callback gaps. Delivered-callback continuity is updated before applying the acceptance gate, so intentional gate skips are not counted as browser callback gaps.
- Browser document visibility and window focus/blur transitions.
- Video `playing`, `waiting`, `stalled`, `timeupdate`, `seeking`, `seeked`, `pause`, `ended`, and related events.
- 100 ms playback heartbeats with `currentTime`, `paused`, `ended`, `readyState`, playback rate, visibility/focus, and playback-quality snapshots.
- Callback silence events, final callback-tail silence, final video `currentTime`, and `ended` state.
- Draw, callback work, encode latency, pending encode queue depth, and frame bytes.

## Correct denominator

Capture coverage is computed from the number of unique `mediaTime` values matched to FFprobe `best_effort_timestamp_time` source-frame indexes, divided by the FFprobe source-frame count. `getVideoPlaybackQuality().totalVideoFrames` and `metadata.presentedFrames` are retained as separate browser diagnostics and are never subtracted from one another.

## Guardrails

- Diagnostic-only; do not load a classifier/checkpoint or production recognition pipeline.
- IoT must remain disabled with an empty mapping and pre-IoT lockdown present.
- Do not modify production source, capture policy, checkpoint, class order, feature dimensions, frame count, or preprocessing.
- Each variant writes to a separate directory and local-only receiver.
- The six files are resolved against the pinned 77-item validation manifest; no validation split is changed.

## Interpretation limits

Four sequential passes can still be affected by system/browser state. Clip order is rotated between variants to reduce position bias. This is a focused six-clip diagnostic, not a production performance guarantee.
