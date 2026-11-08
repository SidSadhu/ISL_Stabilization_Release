import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APP = (ROOT / "src/isl_recognition/static/app.js").read_text(encoding="utf-8-sig")
HTML = (ROOT / "src/isl_recognition/static/index.html").read_text(encoding="utf-8-sig")
LANDMARKS = (ROOT / "src/isl_recognition/static/landmark_diagnostics.js").read_text(encoding="utf-8-sig")


def test_readiness_uses_five_sample_pose_gate():
    assert re.search(
        r"ready:\s*sampleCount\s*===\s*5\s*&&\s*poseDetected\s*>=\s*4\s*&&\s*handDetected\s*>=\s*4",
        LANDMARKS
    )


def test_hand_visibility_is_advisory_before_capture():
    assert "hand visibility is advisory" in LANDMARKS.lower()
    assert "does not block the countdown" in HTML.lower()


def test_arm_then_step_back_interface():
    assert "readinessSetupGraceMs = 6000" in APP
    assert "Cancel wait" in APP
    assert "isl-readiness-wait-cancel" in APP
    assert "MOVE INTO FRAME" in APP


def test_previous_prediction_is_cleared():
    assert "clearPreviousResultForNewAttempt();" in APP
    assert "Waiting for the next capture" in APP


def test_landmark_preview_resumes_after_capture():
    start = LANDMARKS.index("window.addEventListener('isl-capture-state'")
    block = LANDMARKS[start:start + 1200]
    assert "stopDiagnostics(true)" in block
    assert "startDiagnostics()" in block


def test_browser_assets_are_cache_busted():
    assert "20261108-pose-readiness-v2" in HTML