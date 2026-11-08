# Pre-IoT Release Status — Historical Reference

This document preserves the intent of the earlier pre-IoT release, but its item-level status is superseded by the 6 November 2026 implementation. Do not use the old document as the current source of truth.

Current release evidence and next steps are documented in:

- `IMPLEMENTATION_PLAN.md` — full scope, phases and acceptance gates.
- `IMPLEMENTATION_WORKLOG.md` — work performed in the isolated implementation copy.
- `COMPLETION_REPORT.md` — exact tests/checks run and what was not tested.
- `docs/IMPLEMENTATION_STATUS.md` — component-by-component current state.
- `RELEASE_MANIFEST.json` — package scope, assets, checks and safety status.

The current product uses manual Start/Stop isolated-sign capture, retains the complete INCLUDE-50 classifier, and ships with an empty IoT label map. The user must select exactly two labels for any future IoT release. The `.40` text threshold and `.70` IoT gate are both provisional; application-level pre-IoT lockdown remains enabled.
