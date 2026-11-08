# Phase 10B hotfix — pre-play blur race and safe foreground-pass resume

## Observed failure

The completed `foreground_focused` pass was saved 6/6. The following `visible_unfocused` pass repeatedly rejected the first rotated clip (`Adjectives/83. big large/MVI_9396.MOV`) after five attempts. No invalid attempts were uploaded.

## Root cause corrected

Both the browser-side condition validator and the independent Python analyzer opened a visible/unfocused interval on `window_blur` using the event's `video_paused` value. If the user moved focus immediately after starting capture but before the media element emitted `playing`, the `window_blur` event recorded `video_paused=true`. When `video_playing` arrived, the old code only opened an interval if no interval was already open. It therefore left the existing interval permanently tagged `started_during_playback=false`, even when the page then remained visible and unfocused throughout playback.

The fix rebases that interval at the later `video_playing` event. It retains the pre-play blur start and duration for audit, but the qualifying duration begins at playback. Time spent unfocused while the video was paused is never credited toward the 1,500 ms requirement. The browser validator and Python analyzer implement the same rule.

The visible/unfocused rejection message now also reports the longest observed qualifying playback interval and interval count, which should make any future rejection diagnosable from the browser log.

## Preserve the successful foreground pass

The completed foreground capture is reusable; it is not necessary to recapture it. The orchestrator now accepts `--resume-foreground-dir` / `-ResumeForegroundDir`. Before running tests or opening any browser pass, it independently validates:

- phase/mode/run identity (`foreground_focused`)
- 77-row official split and exactly six received target clips
- every container path stays within the capture directory
- all six `.islbrw` payloads parse against their expected official manifest row
- each payload SHA-256 matches its capture-index record
- client-side condition validation is true in every saved clip
- each clip begins with the initial paused frame

If any check fails, the resume is refused and no data is altered. When valid, the completed foreground pass is included in the new result bundle, its prior run logs are copied into the new bundle, and only `visible_unfocused`, `hidden_background`, and `focus_recovery` are captured again.

## Install

Extract this ZIP into the existing project root, overwriting only the five listed script/test files. No application recognizer, checkpoint, feature contract, production configuration or IoT file is changed.

## Execute

1. Find the latest valid `outputs/phase10b_capture_foreground_focused_*` directory with `capture_index.json` showing `received_count=6`, `official_split_count=77`, and `capture_config.phase10b_mode=foreground_focused`.
2. Invoke `scripts/run_phase10b_browser_lifecycle.ps1` with the existing repository, data root, manifest, FFprobe path and `-ResumeForegroundDir` pointing to that directory.
3. The workflow reruns the full project test suite, validates the prior foreground captures, then runs the remaining three conditions. The foreground pass is not repeated.

The `visible_unfocused` procedure remains unchanged: after playback begins, keep the page visible while moving focus to a second application for at least 1.5 seconds. A blur immediately before the `playing` event is now handled correctly, with the measurable exposure starting at `playing`.

## Validation in patch-build environment

- Integrated project suite: 297 passed, 1 skipped.
- Phase 10B focused tests: 26 passed.
- Python compilation: passed.
- Browser inline JavaScript syntax (`node --check`): passed.
- Orchestrator, analyzer and receiver CLI help checks: passed.
- Resume validator smoke tests: valid six-clip pass accepted; false condition evidence rejected.

PowerShell itself was not available in the patch-build environment. The existing PowerShell wrapper is small and only forwards the optional resume path to the Python orchestrator.

## Safety

IoT remains disabled, mappings remain empty, the classifier/checkpoint is not loaded, and the production recognition pipeline is unchanged.
