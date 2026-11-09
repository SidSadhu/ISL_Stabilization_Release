from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

MARKER = "ISL capture-time overlay mode v2"


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the display-only ISL capture-time landmark overlay.")
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()

    js_path = root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js"
    tests_dir = root / "tests"
    package_test = Path(__file__).parent / "tests" / "test_live_landmark_capture_overlay.py"
    if not js_path.is_file():
        fail(f"Could not find landmark diagnostics script: {js_path}")
    if not tests_dir.is_dir():
        fail(f"Could not find tests directory: {tests_dir}")
    if not package_test.is_file():
        fail("The overlay regression test is missing from this patch package.")

    source = js_path.read_text(encoding="utf-8")
    original = source

    if MARKER in source:
        print("The v2 live landmark capture overlay is already installed.")
        test_dest = tests_dir / package_test.name
        if not test_dest.is_file():
            shutil.copy2(package_test, test_dest)
            print(f"Installed missing regression test: {test_dest}")
        print("No source changes were necessary.")
        return

    # Validate the exact installed generation reported by the user. Do not alter
    # this close predicate: it already treats shutdowns outside 'ready' as expected.
    required = [
        "const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) : 10;",
        "const targetWidth = Math.min(480, video.videoWidth);",
        "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);",
        "const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');",
        "window.addEventListener('isl-capture-state', (event) => {",
        "busyState = event.detail?.state || 'ready';",
        "stopDiagnostics(true);",
        "video.addEventListener('loadeddata'",
    ]
    missing = [item for item in required if item not in source]
    if missing:
        fail(
            "This source does not match the inspected version. No files were changed. Missing marker(s):\n  - "
            + "\n  - ".join(missing)
        )

    # 1) During capture, sample the separate diagnostic stream at ~2 Hz. Keep
    # the existing 480px/0.68 JPEG settings to limit CPU contention.
    old_delay = required[0]
    new_delay = (
        "const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) "
        ": (busyState === 'capturing' ? 500 : 10);"
    )
    source = source.replace(old_delay, new_delay, 1)

    # 2) Guard callbacks from a preview WebSocket that was intentionally closed
    # during extraction. An old close event must not null a newly-opened socket.
    socket_create = "socket = new WebSocket(`${scheme}//${location.host}/ws/landmark-preview`);"
    if socket_create not in source:
        fail("Could not locate landmark-preview WebSocket creation. No files were changed.")
    if "const previewSocket = socket;" not in source:
        source = source.replace(socket_create, socket_create + "\n      const previewSocket = socket;", 1)

    listener_specs = [
        ("socket.addEventListener('message', (event) => {", "if (socket !== previewSocket) return;"),
        ("socket.addEventListener('error', () => {", "if (socket !== previewSocket) return;"),
        ("socket.addEventListener('close', (event) => {", "if (socket !== previewSocket) return;"),
    ]
    for anchor, guard in listener_specs:
        if anchor not in source:
            fail(f"Could not locate WebSocket listener: {anchor}. No files were changed.")
        # Do not duplicate guards if a source already contains one.
        pos = source.find(anchor) + len(anchor)
        nearby = source[pos:pos + 120]
        if guard not in nearby:
            source = source[:pos] + "\n        " + guard + source[pos:]

    # 3) Replace only the capture-state listener. Keep the preview session open
    # for 'capturing', and stop it during 'processing' so full-clip extraction
    # retains CPU. 'ready' restarts it after the result arrives.
    listener_anchor = "window.addEventListener('isl-capture-state', (event) => {"
    listener_start = source.find(listener_anchor)
    listener_end = source.find("video.addEventListener('loadeddata'", listener_start)
    if listener_start < 0 or listener_end < 0:
        fail("Could not safely bound the capture-state listener. No files were changed.")

    old_listener = source[listener_start:listener_end]
    if "if (busyState !== 'ready')" not in old_listener or "stopDiagnostics(true);" not in old_listener:
        fail("The inspected stop-on-non-ready lifecycle was not found. No files were changed.")
    if "if (busyState === 'capturing')" in old_listener:
        fail("A capture-time branch already exists; refusing to layer a second patch.")

    new_listener = '''window.addEventListener('isl-capture-state', (event) => {
    // ISL capture-time overlay mode v2: visual-only, throttled during recording.
    busyState = event.detail?.state || 'ready';

    if (busyState === 'processing') {
      // Yield the separate preview MediaPipe session to full-clip extraction.
      stopDiagnostics(true);
      setMessage('Live landmarks are paused during full-clip extraction to protect recognition processing.', 'warn');
      return;
    }

    if (toggle.checked) {
      if (busyState === 'capturing') {
        setMessage('Live landmarks remain visible during recording. This diagnostic overlay is sampled at about 2 updates/second; the recorded recognition stream is separate and unchanged.', 'good');
        if (!socket && !connecting) startDiagnostics();
        else scheduleFrame(500);
        return;
      }
      // On 'ready', reconnect after the processing-time pause. If the socket is
      // already active, startDiagnostics() safely does nothing.
      startDiagnostics();
    }
  });
  '''
    source = source[:listener_start] + new_listener + source[listener_end:]

    if source == original:
        fail("Patch produced no changes. No files were written.")

    # Verify that this is strictly a diagnostics-only patch and key behaviour
    # remains as intended before creating a backup or writing anything.
    if new_delay not in source:
        fail("Capture sampling throttle verification failed. No files were written.")
    if "const targetWidth = Math.min(480, video.videoWidth);" not in source:
        fail("The existing preview resolution unexpectedly changed. No files were written.")
    if "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);" not in source:
        fail("The existing preview image quality unexpectedly changed. No files were written.")
    if "const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');" not in source:
        fail("The existing expected-close predicate changed. No files were written.")
    if "if (busyState === 'capturing')" not in source or "if (busyState === 'processing')" not in source:
        fail("Capture/processing lifecycle verification failed. No files were written.")
    if "if (socket !== previewSocket) return;" not in source:
        fail("Stale socket callback guard verification failed. No files were written.")
    if source.count("if (socket !== previewSocket) return;") < 3:
        fail("Not all stale socket callback guards were installed. No files were written.")
    if "const POSE_CONNECTIONS" not in source or "const HAND_CONNECTIONS" not in source:
        fail("Existing pose/hand drawing primitives were not found. No files were written.")

    # Back up the only production source file that will be modified.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / "backups" / f"live_landmark_overlay_v2_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(js_path, backup_dir / js_path.name)
    js_path.write_text(source, encoding="utf-8", newline="\n")

    test_dest = tests_dir / package_test.name
    if test_dest.exists():
        shutil.copy2(test_dest, backup_dir / test_dest.name)
    shutil.copy2(package_test, test_dest)

    print("Installed v2 live landmark overlay during capture.")
    print(f"Backup folder: {backup_dir}")
    print(f"Updated production file: {js_path}")
    print(f"Installed regression test: {test_dest}")
    print("Capture overlay rate: approximately 2 updates/second.")
    print("Diagnostic preview resolution/quality: unchanged at 480px / JPEG 0.68.")
    print("Processing: preview stops during full-clip extraction and restarts on ready.")
    print("Expected-close predicate: unchanged.")
    print("Not modified: app.js, HTML, CSS, backend, feature extraction, model, checkpoint, threshold, IoT.")


if __name__ == "__main__":
    main()
