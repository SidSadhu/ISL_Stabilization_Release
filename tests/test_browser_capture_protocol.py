from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "browser_capture_protocol.py"
spec = importlib.util.spec_from_file_location("browser_capture_protocol", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def sample_bundle():
    frames = [b"\xff\xd8ISL-JPEG-PAYLOAD\xff\xd9" for _ in range(12)]
    header = {
        "schema_version": 1,
        "capture_config": dict(module.EXPECTED_CONFIG),
        "case": {
            "video_path": "Animals/1. Dog/MVI_3060.MOV",
            "label": "dog",
            "source_name": "MVI_3060.MOV",
            "source_width": 1280,
            "source_height": 720,
            "capture_duration_ms": 600,
            "frame_count": len(frames),
            "timer_ticks": len(frames),
            "duplicate_frame_skips": 0,
            "browser_total_video_frames": 15,
            "browser_dropped_video_frames": 0,
            "frames": [
                {
                    "video_time_seconds": 0.04 * (index + 1),
                    "elapsed_ms": 40.0 * (index + 1),
                    "width": 640,
                    "height": 360,
                    "jpeg_bytes": len(frame),
                }
                for index, frame in enumerate(frames)
            ],
        },
    }
    return header, frames


def test_browser_capture_binary_round_trip_and_official_identity():
    header, frames = sample_bundle()
    encoded = module.encode_capture(header, frames)
    parsed = module.parse_capture_payload(
        encoded,
        {"video_path": "Animals/1. Dog/MVI_3060.MOV", "label": "dog"},
    )
    assert parsed.frames == tuple(frames)
    assert parsed.header["capture_config"]["jpeg_quality"] == 0.92
    assert len(parsed.sha256) == 64


def test_browser_capture_rejects_non_official_path_or_label():
    header, frames = sample_bundle()
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError, match="identity mismatch"):
        module.parse_capture_payload(payload, {"video_path": "Animals/1. Dog/MVI_3060.MOV", "label": "cat"})


def test_browser_capture_rejects_iot_enabled_or_wrong_transform():
    header, frames = sample_bundle()
    header["capture_config"]["iot_enabled"] = True
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError, match="iot_enabled"):
        module.parse_capture_payload(payload)
    header, frames = sample_bundle()
    header["capture_config"]["jpeg_quality"] = 0.72
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError, match="jpeg_quality"):
        module.parse_capture_payload(payload)


def test_browser_capture_rejects_duplicate_timestamps_and_bad_dimensions():
    header, frames = sample_bundle()
    header["case"]["frames"][1]["video_time_seconds"] = header["case"]["frames"][0]["video_time_seconds"]
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError, match="strictly increasing"):
        module.parse_capture_payload(payload)
    header, frames = sample_bundle()
    header["case"]["frames"][0]["height"] = 480
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError, match="dimensions"):
        module.parse_capture_payload(payload)


def test_browser_capture_rejects_path_traversal_and_truncated_payload():
    with pytest.raises(module.CaptureBundleError, match="Unsafe"):
        module.normalize_video_path("../secret.mp4")
    header, frames = sample_bundle()
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError):
        module.parse_capture_payload(payload[:-3])


def test_browser_capture_contract_cannot_exceed_frame_or_capture_limits():
    header, frames = sample_bundle()
    header["case"]["capture_duration_ms"] = 6200
    payload = module.encode_capture(header, frames)
    with pytest.raises(module.CaptureBundleError, match="6-second"):
        module.parse_capture_payload(payload)


def test_phase1_evaluator_gates_diagnostic_hand_override_but_keeps_pose_gate():
    import sys
    from dataclasses import replace

    evaluator_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_browser_canvas_validation.py"
    evaluator_spec = importlib.util.spec_from_file_location("evaluate_browser_canvas_validation_test", evaluator_path)
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    assert evaluator_spec and evaluator_spec.loader
    sys.modules[evaluator_spec.name] = evaluator
    evaluator_spec.loader.exec_module(evaluator)

    from isl_recognition.app_config import Settings
    settings = replace(Settings(), min_capture_frames=12, min_pose_ratio=0.5, min_hand_ratio=0.3, max_extraction_failure_ratio=0.3)
    info = {
        "submitted_frames": 20, "extracted_frames": 19, "pose_ratio": 0.8,
        "hand_ratio": 0.1, "failure_ratio": 1 / 20,
    }
    accepted, override, reason = evaluator.capture_gate(info, settings, diagnostic_mode=True)
    assert accepted is True and override is True and reason is None
    accepted, override, reason = evaluator.capture_gate({**info, "pose_ratio": 0.2}, settings, diagnostic_mode=True)
    assert accepted is False and override is False and "pose ratio" in reason


def test_phase1_feature_difference_reports_pose_and_each_hand_slot():
    import sys

    evaluator_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_browser_canvas_validation.py"
    evaluator_spec = importlib.util.spec_from_file_location("evaluate_browser_canvas_validation_diff_test", evaluator_path)
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    assert evaluator_spec and evaluator_spec.loader
    sys.modules[evaluator_spec.name] = evaluator
    evaluator_spec.loader.exec_module(evaluator)
    baseline = np.zeros(134, dtype=np.float32)
    changed = baseline.copy()
    changed[0:50] = 2
    changed[50:92] = 4
    changed[92:134] = 6
    diff = evaluator.paired_feature_differences(
        {"features_by_frame": [baseline]}, {"features_by_frame": [changed]}
    )
    assert diff["paired_successful_frames"] == 1
    assert diff["groups"]["pose"]["mean_absolute_difference"] == 2
    assert diff["groups"]["hand1"]["mean_absolute_difference"] == 4
    assert diff["groups"]["hand2"]["mean_absolute_difference"] == 6


def _load_evaluator_module():
    evaluator_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_browser_canvas_validation.py"
    evaluator_spec = importlib.util.spec_from_file_location("evaluate_browser_canvas_validation_safety_test", evaluator_path)
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    assert evaluator_spec and evaluator_spec.loader
    sys.modules[evaluator_spec.name] = evaluator
    evaluator_spec.loader.exec_module(evaluator)
    return evaluator


def test_phase1_evaluator_fails_closed_when_iot_is_enabled_or_allowlist_populated(tmp_path):
    from dataclasses import replace
    import json

    evaluator = _load_evaluator_module()
    from isl_recognition.app_config import Settings
    from isl_recognition.labels import load_label_map

    labels_path = Path(__file__).resolve().parents[1] / "config" / "label_map_include50.json"
    labels = load_label_map(labels_path)
    empty_map = tmp_path / "iot_empty.json"
    empty_map.write_text(json.dumps({"schema_version": "1.0", "mappings": {}}), encoding="utf-8")
    base = replace(Settings(), iot_label_map_path=empty_map, iot_default_enabled=False, pre_iot_lockdown=True)
    assert evaluator.verify_diagnostic_iot_lock(base, labels)["iot_mapping_count"] == 0

    enabled = replace(base, iot_default_enabled=True)
    with pytest.raises(evaluator.EvaluationBlocked, match="ISL_IOT_ENABLED is true"):
        evaluator.verify_diagnostic_iot_lock(enabled, labels)

    mapped = tmp_path / "iot_mapped.json"
    mapped.write_text(json.dumps({"schema_version": "1.0", "mappings": {"hello": "LED_BLINK", "thankyou": "LED_BLINK_TWICE"}}), encoding="utf-8")
    configured = replace(base, iot_label_map_path=mapped)
    with pytest.raises(evaluator.EvaluationBlocked, match="allowlist is not empty"):
        evaluator.verify_diagnostic_iot_lock(configured, labels)


def test_capture_server_reports_whether_recapture_is_explicitly_enabled(tmp_path):
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from serve_browser_validation_capture import CaptureServer, Handler

    rows = [{"video_path": "Animals/1. Dog/MVI_3060.MOV", "label": "dog"}]
    server = CaptureServer(("127.0.0.1", 0), Handler, rows=rows, output_dir=tmp_path / "no-overwrite", overwrite=False, max_upload_bytes=1024 * 1024)
    try:
        assert server.snapshot()["overwrite_enabled"] is False
    finally:
        server.server_close()

    overwrite_server = CaptureServer(("127.0.0.1", 0), Handler, rows=rows, output_dir=tmp_path / "overwrite", overwrite=True, max_upload_bytes=1024 * 1024)
    try:
        assert overwrite_server.snapshot()["overwrite_enabled"] is True
    finally:
        overwrite_server.server_close()


def test_capture_receiver_does_not_overwrite_orphaned_file_without_explicit_flag(tmp_path):
    import http.client
    import json
    import sys
    import threading
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from serve_browser_validation_capture import CaptureServer, Handler, safe_capture_id

    header, frames = sample_bundle()
    payload = module.encode_capture(header, frames)
    row = {"video_path": "Animals/1. Dog/MVI_3060.MOV", "label": "dog"}

    def post_capture(server):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("POST", "/api/capture", body=payload, headers={"Content-Type": "application/octet-stream"})
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    protected_dir = tmp_path / "protected"
    protected_server = CaptureServer(("127.0.0.1", 0), Handler, rows=[row], output_dir=protected_dir, overwrite=False, max_upload_bytes=1024 * 1024)
    capture_id = safe_capture_id(1, row)
    clip_path = protected_dir / "clips" / f"{capture_id}.islbrw"
    clip_path.write_bytes(b"orphaned-before-index")
    protected_thread = threading.Thread(target=protected_server.serve_forever, daemon=True)
    protected_thread.start()
    try:
        status, body = post_capture(protected_server)
        assert status == 409 and body["error"] == "duplicate_capture"
        assert clip_path.read_bytes() == b"orphaned-before-index"
        assert protected_server.snapshot()["received_count"] == 0
    finally:
        protected_server.shutdown()
        protected_thread.join(timeout=5)
        protected_server.server_close()

    overwrite_dir = tmp_path / "overwrite-enabled"
    overwrite_server = CaptureServer(("127.0.0.1", 0), Handler, rows=[row], output_dir=overwrite_dir, overwrite=True, max_upload_bytes=1024 * 1024)
    overwrite_clip = overwrite_dir / "clips" / f"{capture_id}.islbrw"
    overwrite_clip.write_bytes(b"replace-me")
    overwrite_thread = threading.Thread(target=overwrite_server.serve_forever, daemon=True)
    overwrite_thread.start()
    try:
        status, body = post_capture(overwrite_server)
        assert status == 200 and body["status"] == "saved"
        assert overwrite_clip.read_bytes() == payload
        assert overwrite_server.snapshot()["received_count"] == 1
        assert (overwrite_dir / "capture_index.json").is_file()
    finally:
        overwrite_server.shutdown()
        overwrite_thread.join(timeout=5)
        overwrite_server.server_close()
