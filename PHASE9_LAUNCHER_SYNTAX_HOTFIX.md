# Phase 9 PowerShell Launcher Syntax Hotfix

## Purpose

Repair a Windows PowerShell parser failure in `scripts/run_phase9_repeatability.ps1` before the Phase 9 tests or browser capture passes start.

## Root cause

The original launcher constructed a diagnostic `throw` message using nested expandable-string subexpressions:

```powershell
throw "Expected six Phase 9 $RunId capture containers; index=$($index.received_count), files=$($clipFiles.Count)."
```

The user's Windows PowerShell parser reported `Missing argument in parameter list` at the second subexpression and then emitted cascading unterminated-string / missing-brace errors.

## Change

The launcher now builds that message using the format operator and simple variables before throwing. Other fragile path/hash output interpolation in the launcher was also converted to explicit variables and `-f` formatting. This avoids nested `$()` expressions in the affected diagnostic/reporting paths and improves compatibility with Windows PowerShell 5.1.

No capture HTML, Python analysis logic, model, checkpoint, validation manifest, feature pipeline, production setting, or IoT configuration is changed.

## Files

- `scripts/run_phase9_repeatability.ps1` — corrected launcher.
- `tests/test_phase9_repeatability.py` — two regression tests for parser-safe formatting.

## Validation

- Phase 9 focused Python tests: 16 passed.
- Python compilation of Phase 9 helper/analysis modules: passed.
- ZIP integrity and SHA-256 manifest: verified during packaging.
- Windows PowerShell itself is not available in the patch-build environment. The actual PowerShell parser/runtime validation occurs when the user runs the corrected launcher on Windows.
