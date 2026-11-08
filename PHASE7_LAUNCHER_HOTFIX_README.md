# Phase 7 PowerShell launcher syntax hotfix

This is a corrective overlay for the Phase 7 patch. The original Phase 7 launcher failed to parse in Windows PowerShell before the test suite or capture passes started.

## Files

- `scripts/run_phase7_capture_completeness.ps1` — rewritten using conservative Windows PowerShell 5.1-compatible syntax, UTF-8 BOM, no embedded here-string, and a separate verifier script.
- `scripts/verify_phase7_bundle.py` — verifies ZIP CRC and every internal SHA-256 entry.
- `tests/test_phase7_capture_completeness.py` — updates the runner-contract assertion to match the explicit argument-quoting helper and argument array.

## Apply

Extract this ZIP over the existing ISL project root. Then rerun the Phase 7 launcher with the same parameters. The previous attempt failed at PowerShell parsing, so it did not start pytest or create either capture pass. No existing capture data is expected to need recovery.

This hotfix does not modify the browser capture HTML, Python capture server, Phase 7 analysis, recognition application, model, checkpoint, feature contract, or IoT settings.
