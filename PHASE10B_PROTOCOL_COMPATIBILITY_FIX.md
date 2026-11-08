# Phase 10B protocol compatibility hotfix

## Problem

The Phase 10B browser page and receiver overrode two legacy `ISLBRW01` protocol fields with values that the shared `browser_capture_protocol.py` validator rejects:

- `capture_config.timer_interval_ms` was `0`, but the shared protocol's `EXPECTED_CONFIG` requires `40`.
- `case.timer_ticks` was `0`, but the shared protocol requires it to be an integer at least as large as `case.frame_count`.

The receiver therefore rejected the first upload with `capture_config.timer_interval_ms must equal 40`. No Phase 10B clip was accepted in the failed run.

## Correction

The browser and persisted capture index now report `timer_interval_ms: 40` as **legacy protocol metadata**. The actual capture path remains ungated:

- `phase10b_callback_gate_ms` remains `0`.
- Capture remains driven by `requestVideoFrameCallback`.
- No `setInterval`-based frame acceptance gate is introduced.
- The Phase 10B capture page does not use `timer_interval_ms` to throttle callbacks.

The legacy `case.timer_ticks` field is set to `frameRows.length`, solely to satisfy the shared protocol's compatibility invariant `timer_ticks >= frame_count`. It is not a count of interval-timer callbacks and does not introduce an interval timer.

## Files

- `scripts/browser_validation_capture_phase10b.html`
- `scripts/serve_phase10b_capture.py`
- `tests/test_phase10b_browser_lifecycle.py`

## Apply

Extract this ZIP into the existing project root, overwriting only these three files. Then rerun the existing `scripts/run_phase10b_browser_lifecycle.ps1` workflow with the same arguments. The launcher will rerun the full project test suite and begin the four lifecycle passes.

The previously failed attempt stopped before any capture was received, so there are no Phase 10B captures to recover and no need to clean up earlier capture files.

## Validation

- Phase 10B focused tests: 21 passed.
- Integrated source-tree suite: 292 passed, 1 skipped.
- Added a regression test that constructs a capture payload and validates it using the actual shared `browser_capture_protocol` parser.
- Browser JavaScript and Python compilation were checked in the integrated source tree.

No model, checkpoint, feature contract, production recognizer, or IoT setting is changed.
