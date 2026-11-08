# Phase 7 Python version-probe hotfix

## Problem
The PowerShell launcher reached its preflight stage but the inline `python -c` version probe failed with a Python syntax error. No project tests or browser capture passes had started.

## Correction
The launcher now calls the standalone `scripts/phase7_python_version.py` file instead of passing Python source through PowerShell's `-c` argument handling. The probe prints only the interpreter major/minor version, which the launcher checks against Python 3.12.

## Files included
- `scripts/run_phase7_capture_completeness.ps1` — corrected launcher.
- `scripts/phase7_python_version.py` — standalone version probe.
- `tests/test_phase7_capture_completeness.py` — updated contract test.

## Apply
Extract this ZIP over the existing project root. Then run the Phase 7 launcher with the same parameters. No existing browser capture needs to be reused; the previous attempt failed before tests and before either capture server was started.

This patch does not modify the Phase 7 browser HTML, server, analysis implementation, recognition application, model, checkpoint, class ordering, feature contract, or IoT configuration.
