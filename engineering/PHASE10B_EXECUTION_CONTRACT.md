# Phase 10B Execution Contract

## Purpose

Validate browser focus/visibility lifecycle effects using four controlled capture conditions, reject unverified exposures before upload, independently revalidate them after capture, and report completed versus right-censored lifecycle intervals correctly.

## Frozen experiment settings

- Source dataset: pinned INCLUDE-50 official validation split; exactly 77 manifest rows.
- Diagnostic subset: six named clips previously used in Phases 3–9.
- Browser transport: 640 px Canvas width, JPEG q92, synchronous `toDataURL`.
- Sampling: ungated `requestVideoFrameCallback`; `metadata.mediaTime` timestamps.
- Initial frame: paused frame at time zero is captured and encoded before `video.play()`.
- Playback heartbeat: 100 ms.
- No classifier/checkpoint load; no IoT; no production recognition changes.

## Condition contract

| Mode | Required evidence | Rejection rule |
|---|---|---|
| foreground_focused | Initial frame and every captured frame visible/focused; no blur or hidden transition | Any focus loss, hidden transition, or non-visible/unfocused frame |
| visible_unfocused | Visible/unfocused interval >= 1,500 ms; starts during playback; no hidden overlap | Missing/short interval or hidden overlap |
| hidden_background | Hidden interval >= 1,500 ms; starts during playback | Missing or short interval |
| focus_recovery | Visible blur/focus interval >= 500 ms, closes visible/focused, then trusted visible/focused click after closure | Missing/short interval, no return focus, untrusted/early/out-of-state marker |

Intervals still open at capture completion are right-censored. Their observed durations are lower bounds, not completed durations. The mode-specific rules above state where a right-censored interval can qualify.

## Retry and abort

- Maximum attempts per clip: five.
- Invalid attempts remain local in the browser and are not POSTed to the receiver.
- Accepted payload includes attempt number, previous failure reasons, mode/run identity, condition evidence and full event/frame rows.
- A failed fifth attempt calls the loopback `/api/abort` endpoint. The orchestrator notices this and fails promptly rather than waiting for the capture timeout.

## Independent analysis

The analyzer recomputes lifecycle intervals from wall-clock epoch timestamps, reclassifies the mode condition, compares client and Python decisions, and verifies source-frame mapping against FFprobe best-effort PTS. PTS validation includes local half-frame tolerance, strictly increasing captured timestamps, strictly increasing source indexes, duplicate checks, and the exact initial-frame/source-index-zero contract.

## Bundle requirements

- 4 capture indexes, one per mode.
- 6 clips per mode, 24 `.islbrw` containers total.
- Official manifest count 77 in each capture index.
- All required report/CSV outputs and the full test log.
- All bundle files except `SHA256SUMS.txt` covered exactly once by the SHA-256 manifest.
- IoT false and classifier/checkpoint not loaded in summary and run-environment metadata.
