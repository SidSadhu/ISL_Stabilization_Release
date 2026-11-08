# Phase 4 PowerShell native-stderr hotfix

## Why this patch is needed

The Phase 4 replay launcher runs under `$ErrorActionPreference = 'Stop'`. MediaPipe/TensorFlow Lite can emit informational or warning messages on native stderr. Windows PowerShell may surface these as `NativeCommandError` records, which can terminate the launcher even when the Python diagnostic is healthy.

## Change

This patch updates only `scripts/run_phase4_controlled_replay.ps1`. Around the Python Phase 4 process, it temporarily sets `$ErrorActionPreference = 'Continue'`, captures `$LASTEXITCODE` immediately after the process returns, and restores the previous preference in a `finally` block. The launcher's real Python exit-code check remains authoritative. All logs continue to be saved to `phase4_runner.log`.

No Python diagnostic, project source, model, checkpoint, preprocessing, manifest, IoT configuration, or production setting is changed.

## Installation

Extract this ZIP into the project root, overwriting the existing launcher. Then rerun the Phase 4 command with the same paths and arguments. The full project test suite will run again before the experiment.
