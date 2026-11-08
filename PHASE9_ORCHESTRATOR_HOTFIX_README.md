# Phase 9 Orchestrator Hotfix

## Why this replaces the prior launcher patch

The previous Phase 9 launcher continued to fail during Windows PowerShell parsing after a targeted line-formatting fix. Rather than issue another line-level change to a large PowerShell function containing nested control flow and interpolated strings, this hotfix moves workflow orchestration into a standalone Python 3.12 script and reduces the PowerShell entrypoint to a short argument-forwarding wrapper.

## Files

- `scripts/run_phase9_repeatability.ps1` — minimal PowerShell wrapper. It checks the project virtual environment and forwards `RepoPath`, `DataRoot`, `Manifest`, and `FfprobePath` to Python.
- `scripts/phase9_orchestrator.py` — runs safety/preflight checks, the complete pytest suite, six capture-server passes, the FFprobe-based analyzer, and result ZIP creation/verification.
- `tests/test_phase9_repeatability.py` — expanded regression tests covering the wrapper/orchestrator split, pass ordering, diagnostic safety, and bundle checksum generation.

## Workflow preserved

Capture pass order remains:

1. `sync_r1`
2. `async_r1`
3. `async_r2`
4. `sync_r2`
5. `sync_r3`
6. `async_r3`

Each pass still uses the existing Phase 9 browser page, six fixed diagnostic clips, 640 px Canvas, JPEG quality 0.92, initial paused-frame capture, ungated `requestVideoFrameCallback`, and the same FFprobe source PTS analyzer.

The official 77-item INCLUDE validation manifest is re-verified against the pinned AI4Bharat commit. Only the six diagnostic clips are captured per pass. IoT must remain disabled, the IoT mapping must remain empty, and the hard pre-IoT lock must remain present. No model/checkpoint is loaded.

## Bundle construction correction

The Python orchestrator stages results, logs, metadata, and six sets of capture containers; creates `SHA256SUMS.txt` for every staged file except the manifest itself; writes the ZIP; and invokes the existing Phase 9 bundle verifier. This avoids a self-referential checksum manifest.

## Validation in the patch-build environment

- Phase 9 focused tests: 20 passed.
- Integrated project suite: 257 passed, 1 skipped.
- Python compilation: passed.
- Python orchestrator CLI: passed.
- PowerShell wrapper static checks (balanced delimiters and forwarding contract): passed.
- ZIP integrity and internal patch manifest: verified.

Windows PowerShell is not installed in the patch-build environment, so the actual Windows PowerShell parser/runtime is not claimed as locally executed. The new PowerShell file is intentionally a small wrapper to reduce parser surface area; the full workflow is in Python and covered by automated tests.
