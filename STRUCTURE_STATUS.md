# Consolidated Release Structure Status — 6 November 2026

The 6 November implementation package contains the current manual INCLUDE-50 web application, IoT allowlist safeguards, official validation tooling, tests and updated handoff documentation. See `IMPLEMENTATION_PLAN.md`, `IMPLEMENTATION_WORKLOG.md`, `COMPLETION_REPORT.md`, and `RELEASE_MANIFEST.json` for current details.

- Product: one isolated sign per manually started/stopped capture.
- Model: full AI4Bharat INCLUDE-50; 134 features per frame; 169-frame model input.
- Primary capture: JPEG frames are buffered during recording; MediaPipe feature extraction runs sequentially after Stop. No bounded asynchronous queue is currently used by the main webapp.
- Text confidence: 0.40 provisional and uncalibrated.
- IoT: separate 0.70 provisional gate; empty default map, exactly two user-selected labels required for a future release; application code lockdown remains on; firmware GPIO default is disabled.
- External assets: AI4Bharat Git clone, model checkpoint and video media are not bundled.

The source package does not contain the user's `.venv`, private `.env`, checkpoint binary, or video archives. Full official validation, target Windows webcam, live broker, firmware compilation and physical hardware tests remain external gates.
