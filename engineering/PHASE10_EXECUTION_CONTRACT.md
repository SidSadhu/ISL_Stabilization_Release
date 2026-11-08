# Phase 10 execution contract

1. Verify Python 3.12, FFprobe, the official 77-row validation manifest and pinned AI4Bharat repository.
2. Verify IoT lockdown and empty mapping; set process `ISL_IOT_ENABLED=false`.
3. Run the full project test suite and preserve the complete log.
4. Execute four separate local capture passes, one per lifecycle mode, each containing the same six diagnostic clips.
5. Every capture starts with the paused frame at time zero, then ungated rVFC playback using synchronous JPEG q92 encoding.
6. In visible-unfocused mode the user keeps the capture page visible while another application owns focus.
7. In hidden-background mode the user switches to a second tab after the browser confirms the initial paused frame was recorded, remains hidden for approximately eight seconds, then returns to the capture page so playback and uploads can finish. Callback suspension while hidden is measured, not treated as failure.
8. In focus-recovery mode the amber per-clip cue requests a one-second focus excursion followed by return and a trusted marker click.
9. Verify each six-clip capture index, parse each `.islbrw` container, match frame timestamps against FFprobe PTS, and calculate lifecycle-aware metrics.
10. Bundle result tables, report, source PTS metadata, capture indexes, binary captures, test logs and server logs with per-file SHA-256 entries; verify the ZIP before reporting success.

No production recognition code, model/checkpoint, class vocabulary/order, 134-D feature contract, 169-frame sequence or IoT setting is modified.
