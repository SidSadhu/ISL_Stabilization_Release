from __future__ import annotations
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

V2_MARKER = "ISL capture-time overlay mode v2"
OLD_PREDICATE = "const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');"
NEW_PREDICATE = "const wasExpected = event.code === 1000 && !toggle.checked;"
OLD_TEST_ASSERT = """assert "const wasExpected = event.code === 1000 && (!toggle.checked || busyState !== 'ready');" in source"""
NEW_TEST_ASSERT = """assert "const wasExpected = event.code === 1000 && !toggle.checked;" in source"""
OLD_CLOSE_BLOCK = """if (!wasExpected && toggle.checked && !failed) {
          setMessage('The local landmark-preview connection closed. Toggle the overlay off and on to reconnect.', 'warn');
        }"""
NEW_CLOSE_BLOCK = """if (!wasExpected && toggle.checked) {
          // A normal close while recording is unexpected too. The v2 predicate
          // masked this case because busyState was not 'ready'.
          failed = false;
          if (busyState === 'processing') {
            setMessage('Live landmark preview disconnected during extraction; it will restart when recognition finishes.', 'warn');
          } else if (reconnectAttempts < MAX_PREVIEW_RECONNECTS) {
            const retryDelay = PREVIEW_RECONNECT_DELAYS_MS[reconnectAttempts];
            reconnectAttempts += 1;
            setMessage(`Live landmark preview disconnected. Reconnecting (${reconnectAttempts}/${MAX_PREVIEW_RECONNECTS})…`, 'warn');
            window.setTimeout(() => {
              if (toggle.checked && !failed && busyState !== 'processing'
                && !socket && !connecting && video.readyState >= 2 && video.videoWidth > 1) {
                startDiagnostics();
              }
            }, retryDelay);
          } else {
            setMessage('Live landmark preview could not reconnect after three attempts. Toggle the overlay off and on to retry.', 'warn');
          }
        }"""


def fail(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}")
    raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    js_path = root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js"
    tests_dir = root / "tests"
    package_test = Path(__file__).parent / "tests" / "test_live_landmark_reconnect.py"

    prior_test_path = tests_dir / "test_live_landmark_capture_overlay.py"
    package_prior_test = Path(__file__).parent / "tests" / "test_live_landmark_capture_overlay.py"
    if not js_path.is_file() or not tests_dir.is_dir() or not package_test.is_file() or not package_prior_test.is_file():
        fail("Project source, tests folder, or bundled regression test is missing. No files changed.")

    source = js_path.read_text(encoding="utf-8")
    prior_test = prior_test_path.read_text(encoding="utf-8") if prior_test_path.is_file() else ""
    if V2_MARKER not in source:
        fail("Expected v2 overlay marker was not found. This patch only targets the installed v2 generation. No files changed.")
    if "let reconnectAttempts = 0;" in source:
        print("Live landmark reconnect recovery is already installed. No production source changes needed.")
        test_dest = tests_dir / package_test.name
        if not test_dest.exists():
            shutil.copy2(package_test, test_dest)
            print(f"Installed regression test: {test_dest}")
        package_prior_text = package_prior_test.read_text(encoding="utf-8")
        if prior_test_path.exists():
            if OLD_TEST_ASSERT in prior_test:
                target_content = prior_test.replace(OLD_TEST_ASSERT, NEW_TEST_ASSERT, 1)
            elif NEW_TEST_ASSERT in prior_test:
                target_content = prior_test
            else:
                fail("Installed overlay regression test does not contain a recognised close assertion.")
        else:
            target_content = package_prior_text
        if not prior_test_path.exists() or prior_test_path.read_text(encoding="utf-8") != target_content:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = root / "backups" / f"live_landmark_overlay_test_alignment_{stamp}"
            backup_dir.mkdir(parents=True, exist_ok=False)
            if prior_test_path.exists():
                shutil.copy2(prior_test_path, backup_dir / prior_test_path.name)
            prior_test_path.write_text(target_content, encoding="utf-8", newline="\n")
            print(f"Regression test backup: {backup_dir}")
        print("Aligned v2 overlay regression test with the corrected close predicate.")
        return
    if OLD_PREDICATE not in source:
        fail("The expected v2 close predicate was not found. No files changed.")
    if OLD_CLOSE_BLOCK not in source:
        fail("The expected v2 close warning block was not found. No files changed.")
    if "const previewSocket = socket;" not in source or "if (socket !== previewSocket) return;" not in source:
        fail("v2 stale-socket guards are missing. No files changed.")
    if "if (busyState === 'capturing')" not in source or "scheduleFrame(500)" not in source:
        fail("v2 capture-time scheduling branch is missing. No files changed.")
    if "if (busyState === 'processing')" not in source or "stopDiagnostics(true);" not in source:
        fail("v2 processing pause branch is missing. No files changed.")
    if prior_test_path.is_file() and OLD_TEST_ASSERT not in prior_test and NEW_TEST_ASSERT not in prior_test:
        fail("The installed v2 overlay test did not match either known assertion. No files changed.")
    if prior_test_path.is_file() and OLD_TEST_ASSERT in prior_test:
        aligned_prior_test = prior_test.replace(OLD_TEST_ASSERT, NEW_TEST_ASSERT, 1)
    elif prior_test_path.is_file():
        aligned_prior_test = prior_test
    else:
        aligned_prior_test = package_prior_test.read_text(encoding="utf-8")

    updated = source.replace(OLD_PREDICATE, NEW_PREDICATE, 1)
    failed_anchor = "  let failed = false;"
    if updated.count(failed_anchor) != 1:
        fail("Could not uniquely locate preview failure state. No files changed.")
    updated = updated.replace(
        failed_anchor,
        failed_anchor + "\n  let reconnectAttempts = 0;\n  const MAX_PREVIEW_RECONNECTS = 3;\n  const PREVIEW_RECONNECT_DELAYS_MS = [500, 1000, 2000];",
        1,
    )
    if updated.count(OLD_CLOSE_BLOCK) != 1:
        fail("Could not uniquely locate the v2 socket close recovery block. No files changed.")
    updated = updated.replace(OLD_CLOSE_BLOCK, NEW_CLOSE_BLOCK, 1)

    toggle_anchor = "  toggle.addEventListener('change', () => {\n    if (toggle.checked) {\n      failed = false;"
    if toggle_anchor not in updated:
        fail("Could not locate overlay toggle reset block. No files changed.")
    updated = updated.replace(
        toggle_anchor,
        "  toggle.addEventListener('change', () => {\n    if (toggle.checked) {\n      failed = false;\n      reconnectAttempts = 0;",
        1,
    )

    capture_anchor = "    busyState = event.detail?.state || 'ready';\n\n    if (busyState === 'processing') {"
    if capture_anchor not in updated:
        fail("Could not locate v2 capture-state handler. No files changed.")
    updated = updated.replace(
        capture_anchor,
        "    busyState = event.detail?.state || 'ready';\n    if (busyState === 'ready') reconnectAttempts = 0;\n\n    if (busyState === 'processing') {",
        1,
    )

    # Static safety checks before write.
    checks = [
        NEW_PREDICATE in updated,
        "const MAX_PREVIEW_RECONNECTS = 3;" in updated,
        "const PREVIEW_RECONNECT_DELAYS_MS = [500, 1000, 2000];" in updated,
        "if (toggle.checked && !failed && busyState !== 'processing'" in updated,
        "if (busyState === 'capturing')" in updated,
        "scheduleFrame(500)" in updated,
        "if (busyState === 'processing')" in updated,
        "const targetWidth = Math.min(480, video.videoWidth);" in updated,
        "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);" in updated,
        "if (socket !== previewSocket) return;" in updated,
        "const POSE_CONNECTIONS" in updated and "const HAND_CONNECTIONS" in updated,
    ]
    if not all(checks):
        fail("Post-patch static verification failed. No files changed.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / "backups" / f"live_landmark_overlay_reconnect_v3_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(js_path, backup_dir / js_path.name)
    test_dest = tests_dir / package_test.name
    if test_dest.exists():
        shutil.copy2(test_dest, backup_dir / test_dest.name)
    if prior_test_path.exists():
        shutil.copy2(prior_test_path, backup_dir / prior_test_path.name)
    js_path.write_text(updated, encoding="utf-8", newline="\n")
    shutil.copy2(package_test, test_dest)
    prior_test_path.write_text(aligned_prior_test, encoding="utf-8", newline="\n")

    print("Installed live landmark preview reconnect recovery v3.")
    print(f"Backup folder: {backup_dir}")
    print(f"Updated production file: {js_path}")
    print(f"Installed regression test: {test_dest}")
    print("Unexpected preview socket closures during recording are now reported and retried up to 3 times.")
    print("Retries: 500 ms, 1 s, 2 s; disabled while full-clip extraction is running.")
    print("Expected close: only a normal close after the user turns the overlay off.")
    print("Not modified: app.js, HTML, CSS, backend, capture sender, feature extraction, model, checkpoint, threshold, IoT.")

if __name__ == "__main__":
    main()
