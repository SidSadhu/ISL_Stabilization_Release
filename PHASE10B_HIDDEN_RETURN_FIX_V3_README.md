# INCLUDE-50 Phase 10B — Hidden Return Lifecycle Fix (v3)

## Why v3 is required

The 2026-11-08 hidden-background diagnostic run (`phase10b_capture_hidden_background_20261108_143227`) received three clips and rejected nine attempts. For the failed White clip, attempts 3–5 contained a real `document.visibilitychange` to `hidden` while playback was active, but the recorded intervals stopped at the video end:

| Attempt | Hidden transition | Video end | Old measured interval |
|---|---:|---:|---:|
| White attempt 3 | 1,165.8 ms | 2,093.6 ms | 927 ms |
| White attempt 4 | 1,140.6 ms | 2,188.3 ms | 1,048 ms |
| White attempt 5 | 1,870.7 ms | 2,089.3 ms | 218 ms |

The browser capture removed its visibility listener shortly after video end. It therefore could not observe the user's later return to the capture tab, despite the page instructions requiring a hidden interval to be closed by that visible return. The accepted clips in that earlier 3/6 pass also contained right-censored intervals, not completed visible-return intervals.

## v3 behavior

- The initial paused frame is still captured and encoded before playback.
- The diagnostic video, 640 px Canvas, synchronous JPEG encoding at quality 0.92, and ungated `requestVideoFrameCallback` sampling are unchanged.
- A hidden interval must begin while playback is active.
- If the video ends while the page remains hidden, capture stays alive and keeps the visibility listener until the user returns to the capture tab.
- The hidden interval is accepted only when the observed `hidden` → `visible` interval is complete, at least 1,500 ms long, and began during playback.
- If no visible return is observed within 10 seconds after the end-drain grace, the capture is finished with right-censored evidence and rejected. The 1,500 ms threshold is not relaxed.
- The server metadata and HTML template now identify condition-enforcement version 2.
- v3 can reuse an independently verified prior `foreground_focused` pass and an independently verified prior `visible_unfocused` pass. The visible-unfocused rule is unchanged by v3.
- The old hidden-background 3/6 pass is not reusable under this completed-interval rule. A fresh 6/6 hidden-background pass is required.
- IoT remains disabled; no classifier, checkpoint, product app, MQTT broker, or ESP32 endpoint is loaded or used.

## Files in this overlay

- `scripts/browser_validation_capture_phase10b.html`
- `scripts/phase10b_browser_lifecycle_analysis.py`
- `scripts/phase10b_browser_lifecycle_orchestrator.py`
- `scripts/serve_phase10b_capture.py`
- `scripts/run_phase10b_browser_lifecycle.ps1`
- `tests/test_phase10b_browser_lifecycle.py`

Expand the ZIP directly into the project root. Do not replace or delete existing output folders.

## Validation performed for this patch

- Focused Phase 10B tests in the patch workspace: 44 passed.
- Python compilation: passed for the modified Python modules.
- Browser JavaScript syntax check with `node --check`: passed.
- ZIP CRC and per-file SHA-256 validation: checked after packaging.

The focused test workspace used a temporary test-only `conftest.py` shim because the user's diagnostic archive does not include all application modules. That shim is not included in this patch. The full Windows project suite and actual browser experiment must be run after installation; this patch has not been represented as already browser-verified.

## Capture procedure for `hidden_background`

1. Select `C:\AIAssets\INCLUDE_videos` and confirm that all six diagnostic clips match.
2. Open a second tab before capture.
3. Start the six-clip pass. After each clip's initial paused frame is recorded and playback starts, switch to the second tab.
4. Stay in the second tab for at least two seconds measured from the hidden transition, then return to the capture tab. If the video ends before that return, the page remains active and waits for the return.
5. A clip is accepted only if the recorded hidden interval began during playback, lasted at least 1,500 ms, and ended with a visible transition.
6. Retry any rejected clip. Keep the PowerShell runner open until the mode reaches 6/6.

When the previous completed visible-unfocused pass is found, the PowerShell workflow below will pass it to the new resume option. It also reuses the existing foreground pass. The hidden-background pass and focus-recovery pass will be newly captured.
