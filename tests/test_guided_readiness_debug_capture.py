from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from isl_recognition import webapp


def test_frame_timing_summary_reports_approximately_25_fps():
    frames = [
        {"timestamp": 1000.00, "jpeg": b"a"},
        {"timestamp": 1000.04, "jpeg": b"b"},
        {"timestamp": 1000.08, "jpeg": b"c"},
        {"timestamp": 1000.12, "jpeg": b"d"},
    ]
    report = webapp._frame_timing_summary(frames)
    assert abs(report["client_timestamp_fps"] - 25.0) < 1e-6
    assert abs(report["frame_interval_median_ms"] - 40.0) < 1e-6
    assert report["frame_interval_count"] == 3


def test_camera_settings_whitelist_does_not_persist_device_identifiers():
    settings = webapp._safe_camera_settings({
        "width": 1280, "height": 720, "frameRate": 25,
        "aspectRatio": 16 / 9, "deviceId": "private-device-id", "groupId": "private-group",
        "facingMode": "user",
    })
    assert settings["width"] == 1280
    assert settings["height"] == 720
    assert "deviceId" not in settings and "groupId" not in settings


def test_debug_capture_is_opt_in_and_saves_frames_features_and_metadata(tmp_path, monkeypatch):
    capture_id = str(uuid.uuid4())
    current = {
        "capture_id": capture_id,
        "jpeg_frames": [
            {"jpeg": b"jpeg-frame-1", "timestamp": 1000.0},
            {"jpeg": b"jpeg-frame-2", "timestamp": 1000.04},
        ],
        "camera_settings": {"width": 1280, "height": 720, "frameRate": 25},
        "expected_label": "hello",
        "frame_timing": {"client_timestamp_fps": 25.0, "frame_interval_median_ms": 40.0},
    }
    metrics = [
        {"frame_index": 0, "extraction_status": "ok", "pose_detected": True, "hand_count": 2},
        {"frame_index": 1, "extraction_status": "ok", "pose_detected": True, "hand_count": 1},
    ]
    features = [np.ones(134, dtype=np.float32), np.ones(134, dtype=np.float32) * 2]
    result = webapp._persist_debug_capture(current, metrics, features, {"pose_ratio": 1.0}, project_root=tmp_path)
    assert result["saved"] is True
    directory = tmp_path / result["directory"]
    assert (directory / "frames" / "frame_0001.jpg").read_bytes() == b"jpeg-frame-1"
    assert (directory / "frames" / "frame_0002.jpg").read_bytes() == b"jpeg-frame-2"
    assert (directory / "raw_features_134d.csv").is_file()
    assert (directory / "interpolated_sequence_134d.csv").is_file()
    assert (directory / "model_input_169x134.csv").is_file()
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["expected_label"] == "hello"
    assert metadata["frame_count_received"] == 2
    assert metadata["frames"][1]["hand_count"] == 1


def test_debug_result_writer_stays_inside_debug_capture_root(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    safe_dir = "outputs/debug_captures/" + str(uuid.uuid4())
    target = tmp_path / safe_dir
    target.mkdir(parents=True)
    webapp._write_debug_result({
        "debug_capture_saved": True,
        "debug_capture_directory": safe_dir,
        "capture_id": "capture-test",
        "predicted_label": "hello",
        "confidence": 0.38,
        "outcome": "rejected",
    })
    payload = json.loads((target / "result.json").read_text(encoding="utf-8"))
    assert payload["predicted_label"] == "hello"
    assert payload["outcome"] == "rejected"


def test_capture_patch_keeps_classifier_contract_and_adds_readiness_and_diagnostics():
    app = (ROOT / "src/isl_recognition/static/app.js").read_text(encoding="utf-8")
    diagnostics = (ROOT / "src/isl_recognition/static/landmark_diagnostics.js").read_text(encoding="utf-8")
    html = (ROOT / "src/isl_recognition/static/index.html").read_text(encoding="utf-8")
    webapp_source = (ROOT / "src/isl_recognition/webapp.py").read_text(encoding="utf-8")
    preprocess = (ROOT / "src/isl_recognition/preprocess.py").read_text(encoding="utf-8")
    assert "aspectRatio: {ideal: 16 / 9}" in app
    assert "waitForStableReadiness" in app
    assert "recentSamples.length > 5" in diagnostics
    assert "handDetected >= 4" in diagnostics
    assert "busyState === \'processing\'" in diagnostics
    assert "busyState === \'capturing\'" in diagnostics
    assert "save_debug:Boolean($('saveDebugCapture')?.checked)" in app
    assert 'id="saveDebugCapture"' in html
    assert 'id="cameraInfo"' in html
    assert '"model_input_169x134.csv"' in webapp_source
    assert "preprocess_feature_sequence(sequence, max_frame_len=169, feature_dim=134)" in webapp_source
    assert "feature_dim: int = 134" in preprocess
    assert '"0.40"' in (ROOT / "src/isl_recognition/app_config.py").read_text(encoding="utf-8")



def test_opt_in_debug_save_websocket_writes_one_local_bundle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_webapp import FakeModel, make_services, send_valid_frames

    project_root = tmp_path / "project"
    monkeypatch.setattr(webapp, "PROJECT_ROOT", project_root)
    svc = make_services(tmp_path, model=FakeModel())
    with TestClient(webapp.create_app(svc)) as client:
        with client.websocket_connect("/ws/capture") as ws:
            ws.receive_json()
            ws.send_json({"type": "start", "camera_settings": {"width": 1280, "height": 720, "frameRate": 25}})
            ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({
                "type": "stop", "save_debug": True, "expected_label": "hello",
                "camera_settings": {"width": 1280, "height": 720, "frameRate": 25},
            })
            while True:
                response = ws.receive_json()
                if response["type"] == "result":
                    break
    result = response["result"]
    assert result["debug_capture_saved"] is True
    assert result["client_timestamp_fps"] > 0
    bundle = project_root / result["debug_capture_directory"]
    assert (bundle / "result.json").is_file()
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["frame_count_received"] == 14
    assert metadata["expected_label"] == "hello"
    assert len(list((bundle / "frames").glob("*.jpg"))) == 14


def test_debug_save_limit_skips_oversized_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "_DEBUG_CAPTURE_MAX_BYTES", 4)
    current = {
        "capture_id": str(uuid.uuid4()),
        "jpeg_frames": [{"jpeg": b"12345", "timestamp": 1.0}],
    }
    result = webapp._persist_debug_capture(current, [], [], {}, project_root=tmp_path)
    assert result["saved"] is False
    assert "limit" in result["error"].lower()
    assert not (tmp_path / "outputs" / "debug_captures").exists()
