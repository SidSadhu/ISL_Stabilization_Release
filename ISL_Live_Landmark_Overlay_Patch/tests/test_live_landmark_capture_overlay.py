from pathlib import Path


def _script() -> str:
    root = Path(__file__).resolve().parents[1]
    path = root / "src" / "isl_recognition" / "static" / "landmark_diagnostics.js"
    return path.read_text(encoding="utf-8")


def test_live_landmarks_remain_active_during_recording_and_pause_for_inference():
    source = _script()
    listener_start = source.index("window.addEventListener('isl-capture-state'")
    loadeddata = source.index("video.addEventListener('loadeddata'", listener_start)
    listener = source[listener_start:loadeddata]

    capture_branch = listener.index("if (busyState === 'capturing')")
    assert "scheduleFrame(350)" in listener[capture_branch:capture_branch + 700]
    assert "stopDiagnostics(true)" not in listener[capture_branch:listener.index("return;", capture_branch)]
    assert "paused during full-clip extraction" in listener
    assert "stopDiagnostics(true)" in listener
    assert "startDiagnostics()" in listener


def test_capture_preview_uses_capture_sized_frames_at_reduced_rate():
    source = _script()
    assert "busyState === 'capturing' ? 350 : 10" in source
    assert "Math.min(busyState === 'capturing' ? 640 : 480, video.videoWidth)" in source
    assert "busyState === 'capturing' ? 0.72 : 0.68" in source
    assert "(!toggle.checked || busyState === 'processing')" in source
    assert "const previewSocket = socket;" in source
    assert "if (socket !== previewSocket || busyState === 'processing') return;" in source
    assert "if (socket !== previewSocket) return;" in source
    assert source.count("if (socket !== previewSocket) return;") >= 2


def test_pose_and_hand_drawers_are_visual_only_and_preserved():
    source = _script()
    assert "const POSE_CONNECTIONS" in source
    assert "const HAND_CONNECTIONS" in source
    assert "drawPoints(pose.slice(0, 25)" in source
    assert "drawPoints(hand" in source
    assert "poseLandmarks" in source
    assert "result.hands" in source
    # The live preview uses its own WebSocket and is not the classifier frame sender.
    assert "/ws/landmark-preview" in source
    assert "jpeg_b64: jpegDataUrl" in source


def test_hand_aware_readiness_gate_remains_intact():
    source = _script().replace(" ", "").replace("\n", "")
    assert "ready:sampleCount===5&&poseDetected>=4&&handDetected>=4" in source
