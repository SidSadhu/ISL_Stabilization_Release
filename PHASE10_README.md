# Phase 10 — Browser lifecycle and focus diagnostics

## Objective

Characterize how browser focus and visibility conditions affect video playback, `requestVideoFrameCallback` delivery, and captured source-frame coverage. Phase 9 showed incomplete coverage coinciding with browser focus/visibility events. Phase 10 isolates four browser lifecycle conditions while keeping the capture implementation fixed.

## Experimental conditions

1. `foreground_focused`: keep the capture page visible and the browser window focused for the whole six-clip pass.
2. `visible_unfocused`: keep the browser page visible side-by-side with another application, then focus the other application while capture continues.
3. `hidden_background`: switch to a second browser tab after starting capture and leave the capture tab hidden until PowerShell reports six uploads.
4. `focus_recovery`: for each clip, wait for the amber cue, switch to a second application for about one second, return to the capture page and click the trusted interaction marker.

All modes use the same fixed capture configuration: 640 px canvas width, JPEG q92, synchronous `toDataURL`, ungated `requestVideoFrameCallback`, paused initial frame capture at currentTime=0, 250 ms end drain, and the existing `ISLBRW01` payload contract.

The six clips are selected from the pinned official 77-row validation manifest. The page rotates clip order by condition to reduce fixed order bias.

## Data collected

- Document visibility, window focus/blur, focusin/focusout, pageshow/pagehide, freeze/resume events.
- Video play, playing, waiting, stalled, suspend, ended, pause, seek and ready-state lifecycle events.
- rVFC callback `mediaTime`, `presentedFrames`, expected display time, callback work and callback gaps.
- 100 ms playback heartbeat samples, currentTime, readyState, visibility/focus, and browser playback-quality counters.
- A user-trusted marker after focus recovery in the recovery condition.
- FFprobe `best_effort_timestamp_time` matching, unique source-frame coverage, duplicate/missing source-frame ranges, and per-callback lifecycle state.

The analysis separates these measurements; browser dropped-frame counters, rVFC `presentedFrames`, source PTS gaps, callback blackouts and heartbeat gaps are not interchangeable.

## Acceptance/interpretation

Mode exposure is explicitly validated. A run that fails to produce the requested exposure is reported as unconfirmed, not silently treated as a successful test. The experiment does not artificially force a browser to keep processing a hidden page; whether callbacks continue or pause is the measured outcome.

This is one within-device diagnostic session across six clips. It is not cross-browser, cross-device or production-wide validation.

## Guardrails

- No classifier/checkpoint is loaded.
- No model, class order, 134-D feature representation, 169-frame inference contract, confidence threshold, production capture path or hand-assignment policy is changed.
- IoT mappings must remain empty, hard pre-IoT lockdown must be present, and the process sets `ISL_IOT_ENABLED=false`.
- Receiver binds to `127.0.0.1` only and exposes only manifest, status and capture endpoints.
- New timestamped capture directories are created. Phase 1–9 evidence is not overwritten.
