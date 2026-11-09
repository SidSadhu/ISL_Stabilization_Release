from __future__ import annotations
import shutil
import sys
from datetime import datetime
from pathlib import Path

EXPECTED_CLOSE = "const wasExpected = event.code === 1000 && !toggle.checked;"
CAPTURE_MARKER = "if (busyState === 'capturing')"
PROCESSING_MARKER = "if (busyState === 'processing')"


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(code)


def segment(text: str, start_marker: str, end_marker: str) -> tuple[int, int, str]:
    start = text.find(start_marker)
    if start < 0:
        fail(f"Could not find source section: {start_marker}")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        fail(f"Could not find end of source section: {end_marker}")
    return start, end, text[start:end]


def main() -> None:
    if len(sys.argv) != 2:
        fail("Usage: apply_overlay_v3_final_fix.py PROJECT_ROOT")
    root = Path(sys.argv[1]).resolve()
    js_path = root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js"
    tests_dir = root / "tests"
    reconnect_test = tests_dir / "test_live_landmark_reconnect.py"
    capture_test = tests_dir / "test_live_landmark_capture_overlay.py"
    if not js_path.is_file() or not tests_dir.is_dir() or not capture_test.is_file():
        fail("Project JavaScript or required v2 regression test is missing. No files changed.")

    source = js_path.read_text(encoding="utf-8")
    if "const MAX_PREVIEW_RECONNECTS = 3;" not in source or EXPECTED_CLOSE not in source:
        fail("The installed v3 reconnect implementation was not found. No files changed.")
    if CAPTURE_MARKER not in source or PROCESSING_MARKER not in source or "scheduleFrame(500)" not in source:
        fail("The v2/v3 capture-state lifecycle was not found. No files changed.")

    updated = source
    changes: list[str] = []
    # These two functions send sampled diagnostic frames. During 'capturing' they
    # must remain eligible; only 'processing' should block them.
    for start_marker, end_marker, label in (
        ("function scheduleFrame(", "function sendDiagnosticFrame(", "scheduleFrame"),
        ("function sendDiagnosticFrame(", "function stopDiagnostics(", "sendDiagnosticFrame"),
    ):
        start, end, block = segment(updated, start_marker, end_marker)
        old_count = block.count("busyState !== 'ready'")
        if old_count:
            block = block.replace("busyState !== 'ready'", "busyState === 'processing'")
            updated = updated[:start] + block + updated[end:]
            changes.append(f"{label}: changed {old_count} ready-only guard(s) to processing-only guard(s)")
        # Re-extract after replacement and ensure the function does not gate on ready.
        _, _, verify_block = segment(updated, start_marker, end_marker)
        if "busyState !== 'ready'" in verify_block:
            fail(f"{label} still blocks all non-ready states. No files changed.")
        if "busyState === 'processing'" not in verify_block:
            fail(f"{label} does not explicitly pause during full-clip processing. No files changed.")

    if "if (socket !== previewSocket) return;" not in updated:
        fail("Stale-socket protection is missing. No files changed.")
    if "const PREVIEW_RECONNECT_DELAYS_MS = [500, 1000, 2000];" not in updated:
        fail("Bounded reconnect backoff is missing. No files changed.")
    if "const targetWidth = Math.min(480, video.videoWidth);" not in updated:
        fail("The expected 480px diagnostic payload is missing. No files changed.")
    if "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);" not in updated:
        fail("The expected diagnostic JPEG quality is missing. No files changed.")

    # Keep the close-handler assertion scoped to the close-handler declaration.
    # busyState guards are valid in the frame scheduler and must not be banned globally.
    test_content = '''from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js").read_text(encoding="utf-8")


def _close_handler_prefix(source: str) -> str:
    marker = "socket.addEventListener('close', (event) => {"
    start = source.index(marker)
    # The close predicate is declared near the top of this listener. Scope the
    # assertion to the listener prefix rather than searching the entire file.
    return source[start:start + 1200]


def test_normal_close_during_capture_is_not_misclassified_as_expected():
    source = _source()
    close_prefix = _close_handler_prefix(source)
    assert "const wasExpected = event.code === 1000 && !toggle.checked;" in close_prefix
    assert "const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');" not in close_prefix
    assert "if (!wasExpected && toggle.checked)" in close_prefix


def test_unexpected_close_reconnects_with_bounded_backoff():
    source = _source()
    assert "const MAX_PREVIEW_RECONNECTS = 3;" in source
    assert "const PREVIEW_RECONNECT_DELAYS_MS = [500, 1000, 2000];" in source
    assert "reconnectAttempts < MAX_PREVIEW_RECONNECTS" in source
    assert "if (toggle.checked && !failed && busyState !== 'processing'" in source
    assert "startDiagnostics();" in source


def test_retry_counter_resets_when_overlay_is_enabled_or_app_returns_ready():
    source = _source()
    assert "reconnectAttempts = 0;" in source
    assert "if (busyState === 'ready') reconnectAttempts = 0;" in source


def test_schedule_frame_allows_capture_but_pauses_processing():
    source = _source()
    start = source.index("function scheduleFrame(")
    end = source.index("function sendDiagnosticFrame(", start)
    scheduler = source[start:end]
    assert "busyState !== 'ready'" not in scheduler
    assert "busyState === 'processing'" in scheduler


def test_send_diagnostic_frame_allows_capture_but_pauses_processing():
    source = _source()
    start = source.index("function sendDiagnosticFrame(")
    end = source.index("function stopDiagnostics(", start)
    sender = source[start:end]
    assert "busyState !== 'ready'" not in sender
    assert "busyState === 'processing'" in sender


def test_capture_overlay_remains_throttled_and_processing_is_paused():
    source = _source()
    assert "if (busyState === 'capturing')" in source
    assert "scheduleFrame(500)" in source
    assert "if (busyState === 'processing')" in source
    assert "stopDiagnostics(true);" in source


def test_recognition_overlay_contract_and_payload_are_unchanged():
    source = _source()
    assert "const targetWidth = Math.min(480, video.videoWidth);" in source
    assert "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);" in source
    assert "jpeg_b64: jpegDataUrl" in source
    assert "if (socket !== previewSocket) return;" in source
    assert "const POSE_CONNECTIONS" in source
    assert "const HAND_CONNECTIONS" in source
'''

    # Ensure close predicate is present in expected close listener, not merely elsewhere.
    marker = "socket.addEventListener('close', (event) => {"
    close_start = updated.find(marker)
    if close_start < 0 or EXPECTED_CLOSE not in updated[close_start:close_start + 1200]:
        fail("Expected close predicate is not inside the WebSocket close handler. No files changed.")

    # Validate the updated source before any writes.
    for function_start, function_end in (
        ("function scheduleFrame(", "function sendDiagnosticFrame("),
        ("function sendDiagnosticFrame(", "function stopDiagnostics("),
    ):
        _, _, block = segment(updated, function_start, function_end)
        if "busyState !== 'ready'" in block or "busyState === 'processing'" not in block:
            fail(f"Capture/processing state gate validation failed in {function_start}. No files changed.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / "backups" / f"live_landmark_overlay_v3_final_fix_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(js_path, backup_dir / js_path.name)
    if reconnect_test.exists():
        shutil.copy2(reconnect_test, backup_dir / reconnect_test.name)

    if updated != source:
        js_path.write_text(updated, encoding="utf-8", newline="\n")
    reconnect_test.write_text(test_content, encoding="utf-8", newline="\n")

    print("Installed final live-overlay capture-gating and regression-test correction.")
    print(f"Backup folder: {backup_dir}")
    print(f"Updated production file: {js_path}" if updated != source else "Production frame gates already correct; no production JS change required.")
    print(f"Updated regression test: {reconnect_test}")
    for change in changes:
        print(f"  {change}")
    if not changes:
        print("  Both diagnostic frame functions already allow 'capturing' and pause only for 'processing'.")
    print("Preserved: close-handler retry logic, 500/1000/2000 ms backoff, 480px/0.68 diagnostic payload, classifier/capture path, model, checkpoint, threshold and IoT.")


if __name__ == "__main__":
    main()
