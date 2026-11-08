# Phase 9 execution contract — first-frame recovery and repeatability

## Objective

Verify whether explicitly capturing the initially paused frame at `currentTime=0` before playback recovers source frame index 0, then compare the repeatability of synchronous `toDataURL` and asynchronous `toBlob` encoding over three repeated runs each.

## Design

- Six previously used diagnostic clips, with the pinned 77-row official validation manifest verified by the shared receiver.
- Two encoder conditions: synchronous `toDataURL('image/jpeg', 0.92)` and asynchronous `toBlob('image/jpeg', 0.92)`.
- Three repeats per encoder, six passes / 36 clip-repeat captures.
- No callback gate. Draw operations remain synchronous inside `requestVideoFrameCallback`.
- Before `video.play()`, after `loadeddata` and a verified seek to zero, draw and encode one paused frame at `currentTime=0`. Store it as frame record 0 with `capture_kind=initial_paused`.
- During playback, use unique `metadata.mediaTime` values from `requestVideoFrameCallback`. A callback at or before the paused frame timestamp is recorded as a duplicate and not added to the payload.
- Wait 250 ms after the video `ended` event so any already-scheduled final-frame callback can be delivered; then flush all asynchronous encodes before uploading.
- All capture payloads retain the established `ISLBRW01` protocol, 640 px Canvas, JPEG q92 and 6-second / 169-frame limits.
- FFprobe `best_effort_timestamp_time` is the source-frame reference. The analysis maps every saved image to the nearest source PTS.

## Repeat order

1. sync repeat 1
2. async repeat 1
3. async repeat 2
4. sync repeat 2
5. sync repeat 3
6. async repeat 3

Clip order is rotated by pass to reduce fixed clip-order bias.

## Acceptance targets (diagnostic targets, not assumed outcomes)

- One and only one initial paused-frame record per clip capture.
- Its timestamp is 0 and it maps to FFprobe source frame index 0 within half a median source-frame interval.
- 100% unique source-frame coverage per clip-repeat run.
- No duplicate FFprobe source-frame matches and no missing interior or terminal source frames.
- Exact captured source-index sets across the three repeats for each clip/encoder group.
- Asynchronous encoding queue is drained at upload time and its maximum depth is bounded (target <= 4).
- Every captured frame maps to source PTS within half a median frame interval.

## Guardrails

- No classifier or checkpoint is loaded.
- No model, class-order, 134-D feature, 169-frame inference contract, confidence threshold, production app path or hand-assignment policy is changed.
- IoT mapping remains empty; the hard pre-IoT lock is checked; `ISL_IOT_ENABLED=false` for the process.
- The receiver binds only to `127.0.0.1` and does not expose MQTT, ESP32 or application endpoints.
- Capture outputs go to new timestamped directories. Prior Phase 1–8 artifacts are not overwritten.

## Interpretation limits

Three repeated runs on one browser/device estimate within-session repeatability only. They do not establish cross-browser, cross-device or production-wide performance. A successful paused-frame record does not by itself prove that the browser's first decoded image is semantically identical to the first FFprobe frame; the PTS match and pixel evidence must be reviewed together.
