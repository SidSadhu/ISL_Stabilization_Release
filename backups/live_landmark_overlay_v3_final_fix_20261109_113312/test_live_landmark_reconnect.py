from pathlib import Path


def _source() -> str:
    # This test is installed in the project tests folder, so resolve project root.
    root = Path(__file__).resolve().parents[1]
    return (root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js").read_text(encoding="utf-8")


def test_normal_close_during_capture_is_not_misclassified_as_expected():
    source = _source()
    assert "const wasExpected = event.code === 1000 && !toggle.checked;" in source
    assert "busyState !== 'ready'" not in source


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
