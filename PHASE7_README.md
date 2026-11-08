# ISL Phase 7 — Capture Completeness and Encoding Back-Pressure

## Purpose

Phase 6 established that the saved browser frames align to the corresponding source video presentation timestamps when `requestVideoFrameCallback` `metadata.mediaTime` is matched to FFprobe `best_effort_timestamp_time`. Phase 6 also recorded gaps between the browser's `presentedFrames` counter values and the saved frames.

Phase 7 tests whether the current capture policy itself contributes to those gaps. It compares two controlled capture modes on the same six evidence-led clips:

| Mode | Callback sampling | Canvas image operation | JPEG encoding |
|---|---|---|---|
| A — `current_40ms` | `requestVideoFrameCallback` with the existing 39 ms minimum interval | Synchronous `drawImage` | Synchronous `toDataURL('image/jpeg', 0.92)` |
| B — `every_presented_frame` | Every unique callback; no minimum interval gate | Synchronous `drawImage` inside that callback | Asynchronous `toBlob('image/jpeg', 0.92)`; callbacks do not wait for encoding completion |

Both modes retain the 640 px Canvas width, JPEG quality 0.92, the six-second clip cap, the 169-frame safety cap, and the same official validation clips. The two modes are saved in different directories and are never allowed to overwrite one another.

## Safety and scope

- Diagnostic capture only. No classifier/checkpoint is loaded.
- No production recognition code or runtime setting is edited.
- The 134-D feature contract, 169-frame model input, checkpoint, label order and hand assignment are not changed.
- IoT/MQTT/ESP32 remains disabled. The local receiver binds to `127.0.0.1` and exposes only the audited browser-capture manifest/status/upload endpoints.
- Capture is performed from the user's local INCLUDE video folder. The patch does not download videos or send them to an external service.
- The original Phase 1/Phase 6 captures are preserved; Phase 7 creates two new timestamped output directories.

## Exact Windows run

1. Download `PHASE7_Capture_Completeness_Patch.zip` to your Downloads folder.
2. Extract it over the existing project root:
   `C:\Users\siddh\Downloads\ISL_Stabilization_Release\ISL_Stabilization_Release`
3. Open PowerShell in that project root and use the complete block supplied in the conversation. The launcher runs the full project test suite before either capture pass.
4. For Mode A, choose `C:\AIAssets\INCLUDE_videos` in the browser page and click **Capture six clips — current 40 ms policy**.
5. When Mode A finishes, the launcher closes its local receiver and opens a second browser page for Mode B. Select the same folder again and click **Capture six clips — every presented frame**.
6. The launcher then runs FFprobe timestamp alignment, computes capture/queue metrics, creates an evidence bundle, and verifies its ZIP CRC and internal SHA-256 manifest.

The browser action is required twice because each mode is independently captured by a fresh receiver into a separate folder. Keep the PowerShell window open. Do not close the browser tab until its pass reaches 6/6.

## Primary measurements

For each clip and each mode, the analysis reports:

- rVFC callback count and actual encoded frame count.
- Browser `totalVideoFrames` and `droppedVideoFrames` deltas.
- `presentedFrames` counter gaps between recorded frames.
- Captured-frame coverage relative to `totalVideoFrames`.
- Callback-to-callback interval p50/p95/max.
- Callback work, synchronous draw duration, and JPEG encode latency.
- Async `toBlob` pending count, mean pending-at-enqueue, and maximum observed concurrent pending encodes.
- Per-frame `mediaTime` to FFprobe `best_effort_timestamp_time` delta, nearest source frame index, and PTS match within half the source median PTS interval.
- SHA-256 identity of each local source video and saved `.islbrw` capture container.

Important distinction: `presentedFrames` is a browser compositor counter, FFprobe PTS indexes decoded source frames, and `totalVideoFrames` is the browser's playback-quality count. These are related but not interchangeable. The report preserves them separately.

## Expected outputs

```text
outputs/
  phase7_capture_current_40ms_YYYYMMDD_HHMMSS/
    capture_index.json
    clips/*.islbrw
  phase7_capture_every_presented_frame_YYYYMMDD_HHMMSS/
    capture_index.json
    clips/*.islbrw
  phase7_capture_completeness_YYYYMMDD_HHMMSS/
    phase7_summary.json
    PHASE7_CAPTURE_COMPLETENESS_REPORT.md
    phase7_clip_comparison.csv
    phase7_frame_metrics.csv
    phase7_mode_pairwise_comparison.csv
    phase7_source_pts_metadata.csv
  PHASE7_RESULT_BUNDLE_YYYYMMDD_HHMMSS.zip
  phase7_run_logs_YYYYMMDD_HHMMSS/
    pytest_full_suite.log
    current_40ms_server.stdout.log
    current_40ms_server.stderr.log
    every_presented_frame_server.stdout.log
    every_presented_frame_server.stderr.log
    phase7_analysis.log
    run_environment.txt
```

The result bundle includes both sets of six capture containers, the two capture indexes, report/CSVs, test log, run metadata, and a SHA-256 manifest.

## Interpretation / decision gate

- If Mode B captures materially more presented frames and lowers the `presentedFrames` gap count while PTS alignment remains strong, the 39 ms gate and synchronous JPEG encode are likely meaningful contributors to capture omissions.
- If Mode B still has frame gaps despite zero browser-reported dropped frames, inspect callback work time, callback gaps, and the `presentedFrames` sequence. Do not assume the decoder is dropping frames.
- If Mode B's async queue or p95 encode latency grows materially, asynchronous encoding may shift the bottleneck into memory/backlog. Do not promote it without a bounded-queue/back-pressure design and repeat test.
- This experiment alone does not approve production changes. Any production capture change requires explicit review, bounded memory handling, regression tests, and validation against the frozen official split.

## Files added

- `scripts/browser_validation_capture_phase7.html`
- `scripts/serve_phase7_capture.py`
- `scripts/phase7_capture_completeness.py`
- `scripts/run_phase7_capture_completeness.ps1`
- `tests/test_phase7_capture_completeness.py`
- `PHASE7_README.md`

No existing file is modified by this patch.
