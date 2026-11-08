# Phase 5 patch — Canvas pixel audit

This is an additive, diagnostic-only patch for the ISL stabilization project.

It follows the Phase 4 evidence: the actual saved Canvas JPEG stream and OpenCV-resized JPEG stream behave differently, particularly on `MVI_0099.MOV` (Good Morning). Phase 5 checks image-level similarity and per-frame static MediaPipe detector parity before any production change is considered.

Files:
- `scripts/phase5_canvas_pixel_audit.py`
- `scripts/run_phase5_canvas_pixel_audit.ps1`
- `tests/test_phase5_canvas_pixel_audit.py`
- `engineering/PHASE5_CANVAS_PIXEL_AUDIT_RUNBOOK.md`

Safety:
- Reads the existing six diagnostic clips and their saved browser captures.
- Verifies the pinned 77-item official validation manifest and repository commit.
- Uses the existing timestamp-aligned source frames; offsets -1/0/+1 are used only for image similarity measurement.
- Uses MediaPipe Hands in static-image mode for the detector replay.
- Does not load the model checkpoint, make classifier predictions, tune the threshold, modify production code, or enable IoT.
- Does not install packages or change `.env` / `config/iot_label_map.json`.

Apply by extracting the ZIP into the existing project root, preserving all local assets and `.venv`, then run the single PowerShell command block provided in the chat.
