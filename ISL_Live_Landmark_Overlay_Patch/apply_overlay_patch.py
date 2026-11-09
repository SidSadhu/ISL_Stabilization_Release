from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
import sys


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()

    js_path = root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js"
    tests_dir = root / "tests"
    if not js_path.is_file():
        fail(f"Could not find the current landmark diagnostics script: {js_path}")
    if not tests_dir.is_dir():
        fail(f"Could not find the project's tests directory: {tests_dir}")

    source = js_path.read_text(encoding="utf-8")
    original = source

    if "// ISL capture-time overlay mode v1" in source:
        print("The live landmark capture overlay patch is already installed.")
        test_path = tests_dir / "test_live_landmark_capture_overlay.py"
        if not test_path.is_file():
            shutil.copy2(Path(__file__).parent / "tests" / test_path.name, test_path)
            print(f"Restored missing regression test: {test_path}")
        return

    # 1) Throttle live diagnostic sampling during recording to about 2.8 Hz.
    old_delay = "const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) : 10;"
    new_delay = "const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) : (busyState === 'capturing' ? 350 : 10);"
    if old_delay not in source:
        fail("Could not locate the expected diagnostic frame scheduler. No source files were changed.")
    source = source.replace(old_delay, new_delay, 1)

    # 2) Match the main capture stream's 640 px processing width while recording.
    old_width = "const targetWidth = Math.min(480, video.videoWidth);"
    new_width = "const targetWidth = Math.min(busyState === 'capturing' ? 640 : 480, video.videoWidth);"
    if old_width not in source:
        fail("Could not locate the expected 480 px diagnostic frame size. No source files were changed.")
    source = source.replace(old_width, new_width, 1)

    old_quality = "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);"
    new_quality = "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', busyState === 'capturing' ? 0.72 : 0.68);"
    if old_quality not in source:
        fail("Could not locate the expected diagnostic JPEG quality setting. No source files were changed.")
    source = source.replace(old_quality, new_quality, 1)

    # Treat the intentional processing-time socket shutdown as expected, so it
    # does not briefly display a misleading connection-warning message.
    old_close = "const wasExpected = event.code === 1000 && !toggle.checked;"
    new_close = "const wasExpected = event.code === 1000 && (!toggle.checked || busyState === 'processing');"
    if old_close not in source:
        fail("Could not locate the expected diagnostics close handler. No source files were changed.")
    source = source.replace(old_close, new_close, 1)

    # Guard asynchronous callbacks from an old preview socket after processing
    # has closed it and the next ready state has created a new preview session.
    socket_create = "socket = new WebSocket(`${scheme}//${location.host}/ws/landmark-preview`);"
    if socket_create not in source:
        fail("Could not locate the local landmark preview WebSocket creation. No source files were changed.")
    if "const previewSocket = socket;" not in source:
        source = source.replace(socket_create, socket_create + "\n      const previewSocket = socket;", 1)

    message_listener = "socket.addEventListener('message', (event) => {"
    if message_listener not in source:
        fail("Could not locate the landmark preview message listener. No source files were changed.")
    source = source.replace(
        message_listener,
        message_listener + "\n        if (socket !== previewSocket || busyState === 'processing') return;",
        1,
    )

    error_listener = "socket.addEventListener('error', () => {"
    if error_listener not in source:
        fail("Could not locate the landmark preview error listener. No source files were changed.")
    source = source.replace(
        error_listener,
        error_listener + "\n        if (socket !== previewSocket) return;",
        1,
    )

    close_listener = "socket.addEventListener('close', (event) => {"
    if close_listener not in source:
        fail("Could not locate the landmark preview close listener. No source files were changed.")
    source = source.replace(
        close_listener,
        close_listener + "\n        if (socket !== previewSocket) return;",
        1,
    )

    # 3) Preserve the existing MediaPipe preview session only while camera frames
    # are being recorded. Keep the current processing-time shutdown behaviour.
    listener_anchor = "window.addEventListener('isl-capture-state', (event) => {"
    listener_start = source.find(listener_anchor)
    if listener_start < 0:
        fail("Could not locate the capture-state listener. No source files were changed.")

    state_line = "busyState = event.detail?.state || 'ready';"
    state_pos = source.find(state_line, listener_start)
    if state_pos < 0 or state_pos > listener_start + 900:
        fail("Could not locate the capture-state assignment safely. No source files were changed.")

    capture_branch = """\n    // ISL capture-time overlay mode v1: keep the existing local MediaPipe preview alive\n    // while the browser records frames. Sample slowly to minimise CPU contention.\n    if (busyState === 'capturing') {\n      setMessage('Live landmarks are active during recording (sampled at a reduced rate to protect capture throughput). They pause during full-clip extraction.', 'good');\n      scheduleFrame(350);\n      return;\n    }\n"""

    # Insert before the existing generic non-ready branch which closes the preview.
    not_ready = "if (busyState !== 'ready') {"
    branch_check_start = state_pos + len(state_line)
    not_ready_pos = source.find(not_ready, branch_check_start, branch_check_start + 1400)
    if not_ready_pos < 0:
        # Some releases may already use a processing-only branch. In that case add
        # the capture branch before the first processing condition instead.
        processing_check = "if (busyState === 'processing') {"
        not_ready_pos = source.find(processing_check, branch_check_start, branch_check_start + 1400)
    if not_ready_pos < 0:
        fail("Could not find the existing capture/processing lifecycle branch. No source files were changed.")
    insertion_line_start = source.rfind("\n", 0, not_ready_pos) + 1
    source = source[:insertion_line_start] + capture_branch + source[insertion_line_start:]

    # Keep the current post-processing restart logic. If this build does not already
    # restart the socket on 'ready', add a guarded restart at the end of this listener.
    listener_end_search = source.find("video.addEventListener('loadeddata'", listener_start)
    if listener_end_search < 0:
        fail("Could not identify the end of the capture-state listener safely. No source files were changed.")
    listener_segment = source[listener_start:listener_end_search]
    if "startDiagnostics()" not in listener_segment:
        close = source.rfind("  });", listener_start, listener_end_search)
        if close < 0:
            fail("Could not find the capture-state listener closing boundary. No source files were changed.")
        restart = """    if (busyState === 'ready' && toggle.checked && !socket) {\n      startDiagnostics();\n    }\n"""
        source = source[:close] + restart + source[close:]

    # Add a message when full-clip extraction pauses the preview. The recognizer is untouched.
    listener_start = source.find(listener_anchor)
    listener_end_search = source.find("video.addEventListener('loadeddata'", listener_start)
    listener_segment = source[listener_start:listener_end_search]
    stop_marker = "stopDiagnostics(true);"
    if stop_marker in listener_segment and "paused during full-clip extraction" not in listener_segment:
        absolute_stop = source.find(stop_marker, listener_start, listener_end_search)
        source = (
            source[:absolute_stop]
            + "setMessage('Live landmarks are paused during full-clip extraction to protect recognition processing. They will resume when processing finishes.');\n      "
            + source[absolute_stop:]
        )

    if source == original:
        fail("Patch made no changes. No files were written.")

    # Validate key properties before writing any changed project file.
    if "busyState === 'capturing' ? 350 : 10" not in source:
        fail("Capture sampling throttle verification failed. No files were written.")
    if "Math.min(busyState === 'capturing' ? 640 : 480, video.videoWidth)" not in source:
        fail("Capture preview resolution verification failed. No files were written.")
    capture_index = source.find("if (busyState === 'capturing')")
    generic_index = source.find("if (busyState !== 'ready')", capture_index)
    if generic_index >= 0 and capture_index > generic_index:
        fail("Capture branch was inserted after the shutdown branch. No files were written.")
    if "ready: sampleCount === 5 && poseDetected >= 4 && handDetected >= 4" not in source:
        # Whitespace may vary, but the semantic gate must remain in the current file.
        compact = "".join(source.split())
        if "ready:sampleCount===5&&poseDetected>=4&&handDetected>=4" not in compact:
            fail("Expected hand-aware readiness gate was not found; refusing to patch a different readiness implementation.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / "backups" / f"live_landmark_overlay_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(js_path, backup_dir / js_path.name)
    js_path.write_text(source, encoding="utf-8", newline="\n")

    test_source = Path(__file__).parent / "tests" / "test_live_landmark_capture_overlay.py"
    if not test_source.is_file():
        fail("Patch regression test is missing from the patch package.")
    test_dest = tests_dir / test_source.name
    if test_dest.exists():
        shutil.copy2(test_dest, backup_dir / test_dest.name)
    shutil.copy2(test_source, test_dest)

    print("Installed live landmark overlay during recording.")
    print(f"Backup folder: {backup_dir}")
    print(f"Updated: {js_path}")
    print(f"Installed regression test: {test_dest}")
    print("Capture preview: 640 px, reduced JPEG quality, approximately 2.8 updates/s maximum.")
    print("Processing: preview pauses; existing diagnostics restart on ready.")
    print("Unchanged: app.js, HTML, CSS, backend, classifier, 134-D features, checkpoint, thresholds, IoT.")


if __name__ == "__main__":
    main()
