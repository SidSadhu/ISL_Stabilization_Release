# Phase 10B — Verified Browser Lifecycle Capture

Phase 10B is a diagnostic-only replacement for the Phase 10 browser lifecycle pass. It addresses the invalid experimental exposures and hidden-duration reporting defect found in the first Phase 10 run.

## Scope and safety

- Uses the pinned INCLUDE-50 official validation manifest (77 rows) and the same six evidence-led diagnostic clips.
- Uses the existing browser Canvas capture contract: 640 px width, JPEG quality 0.92, synchronous `toDataURL`, ungated `requestVideoFrameCallback`, `metadata.mediaTime`, and an initial paused frame captured before playback.
- Does not load the classifier/checkpoint, modify model weights, class order, feature construction, production capture settings, confidence thresholds, or enable IoT.
- Receiver binds to `127.0.0.1` only and accepts only captures carrying a successful client-side condition validation. The offline analyzer independently checks the lifecycle evidence again.
- Invalid condition attempts are not uploaded. The browser exposes a per-clip Retry action, with a maximum of five attempts; exhausting retries aborts the pass explicitly.

## Four enforced conditions

1. `foreground_focused`: the initial frame and every captured frame must be visible and focused; any blur or hidden transition rejects the attempt.
2. `visible_unfocused`: at least 1,500 ms of visible-but-unfocused time must occur during playback. A still-open interval at capture finish is accepted only as a right-censored lower bound if no hidden transition overlaps it.
3. `hidden_background`: at least 1,500 ms hidden time must begin during playback. A still-open interval at capture finish is recorded as a right-censored lower bound.
4. `focus_recovery`: at least 500 ms of visible blur/focus loss must end with the page visible and focused, followed by a trusted user click while visible and focused.

Each condition is checked in the browser before upload and independently in Python after capture. The report records client/Python agreement and exposes any disagreement.

## Hidden and focus interval semantics

Intervals use `wall_time_epoch_ms`, not page-relative time. If an opening event has no corresponding closing event before capture finishes, the report stores:

- `censored=true`
- `end_reason=capture_end_right_censored`
- `duration_ms` as a lower bound only
- `duration_semantics=right_censored_lower_bound`

It never reports an open interval as a completed duration or as zero. Completed intervals and right-censored lower bounds are reported separately.

## Source timestamp validation

Every captured frame is mapped to FFprobe `best_effort_timestamp_time`. Analysis records the nearest source-frame index, signed timestamp delta, local half-frame tolerance, duplicate source matches, monotonicity, missing frames and initial-frame validity. The initial paused frame must be the first record, have time zero, and map to source frame zero.

## Run on Windows

From the project root, run the Phase 10B PowerShell wrapper with the local AI4Bharat repository, INCLUDE asset folder, pinned validation manifest, and FFprobe executable. The wrapper is deliberately small and forwards all orchestration to Python to avoid complex PowerShell parsing and native-stderr handling issues.

The launcher runs the complete project test suite, then opens four loopback browser capture passes sequentially. For each pass, select `C:\AIAssets\INCLUDE_videos`, confirm all six target clips match, and follow the visible instruction. Invalid attempts are retried within the browser; do not bypass the Retry action.

### User procedure per mode

- **Foreground focused:** keep the capture tab visible and the browser window focused for the entire clip. Do not switch applications or tabs.
- **Visible unfocused:** arrange the browser and a second application side-by-side. After playback starts, click the second application and keep the capture page visible but unfocused for at least 1.5 seconds. Do not minimize or cover the browser page. It is okay to stay unfocused across subsequent clips.
- **Hidden background:** after each clip's initial paused frame is recorded, switch to a second tab for at least 2 seconds, then return to the capture tab. Each clip must contain at least 1.5 seconds of hidden time while playback is active.
- **Focus recovery:** after the amber prompt appears, switch to a second application for at least 0.5 seconds, return to the capture page, then click **Log focus recovery interaction**. The marker must be trusted, visible and focused.

If a clip fails its condition check, the page explains why and offers a Retry button. If the fifth attempt fails, the pass is aborted and the orchestrator stops; it will not upload an invalid clip or silently count it as a successful exposure.

## Outputs

The result ZIP contains:

- `results/PHASE10B_BROWSER_LIFECYCLE_REPORT.md`
- `results/phase10b_summary.json`
- mode, clip, frame, event, heartbeat, hidden-interval, visible-unfocused-interval, and source-PTS CSVs
- four capture indexes and 24 `.islbrw` containers
- complete test/server/analysis logs
- run environment metadata and a SHA-256 manifest covering every other bundle file

The bundle verifier checks ZIP CRC, every SHA-256 entry, all four mode identities, all 24 capture containers, the 77-row official manifest count, and safety metadata.

## Validation status before handoff

- Integrated project suite: 288 passed, 1 skipped (local build environment).
- Phase 10B-focused tests: 19 passed.
- Python compilation, CLI help checks and browser inline-JavaScript syntax check passed.
- The Windows PowerShell wrapper was not executed in the build environment; the user-side launcher runs the actual test suite before capture.
