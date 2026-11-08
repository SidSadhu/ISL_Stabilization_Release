# Phase 9 — first-frame recovery and repeatability

Phase 9 tests the two ungated encoders selected from Phase 8, with an explicit first-frame capture before playback and three repeats per encoder.

## Download / install

Overlay this patch ZIP into the existing Phase 2 stabilization project root. It adds only new `scripts/`, `tests/`, and `engineering/` files; it does not replace application or model files.

## Run

From the project root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\run_phase9_repeatability.ps1 `
  -RepoPath 'C:\AIAssets\INCLUDE' `
  -DataRoot 'C:\AIAssets\INCLUDE_videos' `
  -Manifest '.\outputs\include50_official_val_manifest.csv' `
  -FfprobePath 'C:\Users\<user>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin\ffprobe.exe'
```

The launcher performs Python/FFprobe preflight and the full test suite, then opens six consecutive browser capture pages. For each page choose `C:\AIAssets\INCLUDE_videos`, verify 6/6 matches, and press the displayed capture button. PowerShell advances only after 6/6 captures are accepted.

## Outputs

- `PHASE9_RESULT_BUNDLE_*.zip`
- `PHASE9_REPEATABILITY_REPORT.md`
- `phase9_summary.json`
- `phase9_clip_repeat_metrics.csv`
- `phase9_frame_metrics.csv`
- `phase9_repeatability.csv`
- `phase9_encoder_summary.csv`
- `phase9_encoder_comparison.csv`
- `phase9_event_timeline.csv`
- `phase9_playback_heartbeats.csv`
- `phase9_source_pts_metadata.csv`

The bundle includes 36 `.islbrw` clip captures, six capture indexes, logs, run metadata and `SHA256SUMS.txt`.

## Guardrails

Diagnostic only; no classifier/checkpoint; IoT disabled; no production code or settings changes. The detailed contract is `engineering/PHASE9_EXECUTION_CONTRACT.md`.
