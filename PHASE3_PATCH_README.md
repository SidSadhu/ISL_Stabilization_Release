# Phase 3 hand diagnostics patch

This is an additive diagnostics patch for the existing ISL Stabilization Release project.

## Files installed

- `scripts/phase3_hand_assignment_diagnostics.py` — offline Phase 3 runner.
- `scripts/run_phase3_hand_diagnostics.ps1` — Windows one-command orchestration: full test suite, Phase 3 run, persistent logs, SHA256 manifest, ZIP creation, ZIP CRC test.
- `tests/test_phase3_hand_assignment_diagnostics.py` — focused unit tests.
- `engineering/PHASE3_HAND_ASSIGNMENT_RUNBOOK.md` — protocol, clip selection, outputs and interpretation rules.

## Installation

Extract this ZIP into the **project root** of `ISL_Stabilization_Release` and allow the new files to be placed in their relative folders. All files in this patch are new. It does not replace application source, the model/checkpoint, class map, project environment, or browser captures.

Then run `scripts/run_phase3_hand_diagnostics.ps1` from PowerShell. The defaults match the previously configured local paths:

- `C:\AIAssets\INCLUDE`
- `C:\AIAssets\INCLUDE_videos`
- `outputs\include50_official_val_manifest.csv`
- `outputs\browser_canvas_validation`

The PowerShell runner requires the existing `.venv` Python 3.12 environment. It will stop if IoT is enabled or the IoT mapping is not empty. It does not change `.env` or install packages.


## Phase 3 terminal-frame correction

The runner explicitly handles a browser `video.currentTime` that reaches the container duration and rounds to `frame_count`, even though the last valid decoded index is `frame_count - 1`. It reuses the final source frame only within a half-frame terminal allowance, flags every such sample in `phase3_per_frame_hand_diagnostics.csv`, and reports per-clip counts/timestamps. Decode failures before the final frame and timestamps beyond the terminal allowance still fail closed. The PowerShell launcher temporarily uses `Continue` only around the native Python process and checks `$LASTEXITCODE`; MediaPipe stderr warnings are not treated as the diagnostic result.
