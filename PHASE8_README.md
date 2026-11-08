# Phase 8 — callback lifecycle and 2×2 factor isolation

Phase 8 follows the Phase 7 capture-completeness result. It separates the two variables that Phase 7 changed together: the 39 ms callback-acceptance gate and the JPEG encoder.

## Four independent capture passes

| Mode | Callback gate | JPEG encoder |
|---|---|---|
| `gate_sync` | 39 ms | synchronous `canvas.toDataURL` |
| `ungate_sync` | none | synchronous `canvas.toDataURL` |
| `gate_async` | 39 ms | asynchronous `canvas.toBlob` |
| `ungate_async` | none | asynchronous `canvas.toBlob` |

All modes use `requestVideoFrameCallback`, synchronous `drawImage` in the callback, a 640 px Canvas, JPEG quality 0.92, the same six diagnostic videos, and FFprobe `best_effort_timestamp_time` for source-frame coverage.

## Run on Windows

1. Extract this patch into the project root, preserving the folder structure.
2. Open PowerShell in the project root.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& '.\scripts\run_phase8_callback_lifecycle.ps1' `
    -RepoPath 'C:\AIAssets\INCLUDE' `
    -DataRoot 'C:\AIAssets\INCLUDE_videos' `
    -Manifest '.\outputs\include50_official_val_manifest.csv' `
    -FfprobePath 'C:\Users\siddh\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffprobe.exe'
```

The runner executes the full test suite, then opens one browser page for each mode. For every pass, select `C:\AIAssets\INCLUDE_videos`, confirm all six diagnostic clips are matched, and click the mode-specific capture button. Keep PowerShell open until each pass reports 6/6.

## Outputs

The runner creates four separate capture directories, a report/CSV analysis directory, run logs, and `outputs\PHASE8_RESULT_BUNDLE_*.zip`. The ZIP includes the four capture indexes and six `.islbrw` capture files per mode, result CSVs, event/heartbeat telemetry, logs, environment metadata, and an internal SHA-256 manifest.

## Measurement rules

- Coverage = unique FFprobe source-frame indexes matched from captured `metadata.mediaTime` / source frame count.
- `metadata.presentedFrames` is analysed only against consecutive `metadata.presentedFrames` observations.
- `getVideoPlaybackQuality().totalVideoFrames` and `.droppedVideoFrames` remain independent contextual counters; they are never subtracted from `metadata.presentedFrames`.
- Callback delivery gaps are updated on every delivered callback before the acceptance gate. Intentional gate rejections are therefore counted separately from browser callback gaps.
- Visibility, focus, video lifecycle events, playback heartbeats, callback silences, draw/work duration, encode latency, and pending async encoder depth are recorded.

## Guardrails

This is diagnostic-only. The classifier/checkpoint is not loaded. Production recognition, checkpoint, class order, 134-D feature contract, 169-frame input, preprocessing, confidence settings and IoT configuration are not modified. The receiver binds to `127.0.0.1` only. IoT remains disabled and the zero-entry mapping / pre-IoT lockdown are required.
