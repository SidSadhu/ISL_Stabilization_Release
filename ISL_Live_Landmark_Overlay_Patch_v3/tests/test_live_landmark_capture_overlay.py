from pathlib import Path
import re


def _script() -> str:
    root = Path(__file__).resolve().parents[1]
    path = root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js"
    return path.read_text(encoding="utf-8")


def test_live_landmarks_remain_enabled_during_capture_and_pause_during_processing():
    source = _script()
    start = source.index("window.addEventListener('isl-capture-state'")
    end = source.index("video.addEventListener('loadeddata'", start)
    listener = source[start:end]

    capture = listener.index("if (busyState === 'capturing')")
    processing = listener.index("if (busyState === 'processing')")
    assert processing < capture
    assert "scheduleFrame(500)" in listener
    capture_branch = listener[capture:listener.index("return;", capture)]
    assert "stopDiagnostics(true)" not in capture_branch
    assert "startDiagnostics()" in listener
    assert "stopDiagnostics(true)" in listener[processing:capture]


def test_preview_rate_is_throttled_only_during_capture():
    source = _script()
    assert (
        "const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) "
        ": (busyState === 'capturing' ? 500 : 10);"
    ) in source
    # Preserve the existing low-cost diagnostic image settings.
    assert "const targetWidth = Math.min(480, video.videoWidth);" in source
    assert "const jpegDataUrl = sampleCanvas.toDataURL('image/jpeg', 0.68);" in source


def test_stale_preview_socket_events_cannot_affect_a_new_session():
    source = _script()
    assert "const previewSocket = socket;" in source
    assert source.count("if (socket !== previewSocket) return;") >= 3


def test_existing_overlay_drawer_and_expected_close_logic_are_preserved():
    source = _script()
    assert "const POSE_CONNECTIONS" in source
    assert "const HAND_CONNECTIONS" in source
    assert "drawPoints(pose.slice(0, 25)" in source
    assert "drawPoints(hand" in source
    assert "const wasExpected = event.code === 1000 && !toggle.checked;" in source
    assert "/ws/landmark-preview" in source


def test_patch_does_not_add_model_or_capture_path_dependencies():
    source = _script()
    assert "jpeg_b64: jpegDataUrl" in source
    assert source.count("ISL capture-time overlay mode v2") == 1
