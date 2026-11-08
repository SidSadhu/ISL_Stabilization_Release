"""Regression tests for per-hand diagnostics and the explicitly opt-in debug gate."""
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from test_webapp import FakeExtractor, FakeModel, FakeMQTT, make_services, send_valid_frames
from isl_recognition.webapp import create_app


def test_capture_result_includes_per_hand_diagnostic_metrics(tmp_path):
    model = FakeModel()
    svc = make_services(tmp_path, model=model)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'})
            ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result':
                    break

    result = msg['result']
    assert result['pose_ratio'] == 1.0
    assert result['hand_ratio'] == 1.0
    assert result['hand1_ratio'] == 1.0
    assert result['hand2_ratio'] == 0.0
    assert result['both_hand_ratio'] == 0.0
    assert result['diagnostic_mode'] is False
    assert result['diagnostic_override_used'] is False
    assert result['feature_extraction_latency_ms'] is not None
    assert result['mediapipe_p95_latency_ms'] is not None
    assert model.calls == 1


def test_low_nonzero_hand_coverage_diagnostic_mode_runs_model_and_suppresses_iot(tmp_path):
    class IntermittentHandExtractor(FakeExtractor):
        def extract(self, jpeg, session):
            self.extracted += 1
            self.inputs.append(jpeg)
            found = self.extracted <= 3
            return SimpleNamespace(
                features=np.ones((134,), dtype=np.float32),
                pose_found=True,
                hand_count=1 if found else 0,
                hand1_found=found,
                hand2_found=False,
            )

    model = FakeModel(confidence=.95)
    svc = make_services(tmp_path, model=model, extractor=IntermittentHandExtractor())
    svc.mqtt = FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable', json={'enabled': True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'})
            ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop', 'diagnostic_mode': True})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result':
                    break

    result = msg['result']
    assert result['outcome'] == 'accepted'
    assert result['hand_ratio'] < .5
    assert result['diagnostic_mode'] is True
    assert result['diagnostic_override_used'] is True
    assert msg['iot']['status'] == 'suppressed'
    assert svc.mqtt.commands == []


def test_low_hand_coverage_without_diagnostic_mode_still_rejects_before_inference(tmp_path):
    class IntermittentHandExtractor(FakeExtractor):
        def extract(self, jpeg, session):
            self.extracted += 1
            self.inputs.append(jpeg)
            found = self.extracted <= 3
            return SimpleNamespace(
                features=np.ones((134,), dtype=np.float32),
                pose_found=True,
                hand_count=1 if found else 0,
                hand1_found=found,
                hand2_found=False,
            )

    model = FakeModel()
    svc = make_services(tmp_path, model=model, extractor=IntermittentHandExtractor())
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'})
            ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result':
                    break

    assert msg['result']['outcome'] == 'invalid_capture'
    assert msg['result']['hand_ratio'] < .5
    assert msg['result']['diagnostic_mode'] is False
    assert model.calls == 0


def test_live_landmark_preview_returns_local_detector_landmarks_without_cdn(tmp_path):
    class DiagnosticExtractor(FakeExtractor):
        def extract(self, jpeg, session):
            self.extracted += 1
            pose = [
                {"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.95}
                for _ in range(33)
            ]
            hand = [
                {"x": 0.3 + index * 0.01, "y": 0.4, "z": 0.0, "visibility": 1.0}
                for index in range(21)
            ]
            return SimpleNamespace(
                features=np.ones((134,), dtype=np.float32),
                pose_found=True,
                hand_count=1,
                hand1_found=True,
                hand2_found=False,
                pose_landmarks=pose,
                hands=[hand],
                hand_labels=["Left"],
                frame_width=320,
                frame_height=240,
            )

    svc = make_services(tmp_path, model=FakeModel(), extractor=DiagnosticExtractor())
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/landmark-preview') as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["detector"] == "local-python-mediapipe"
            ws.send_json({
                "type": "frame",
                "timestamp": 123.0,
                "width": 320,
                "height": 240,
                "jpeg_b64": "ZmFrZS1qcGVn",
            })
            result = ws.receive_json()

    assert result["type"] == "result"
    assert result["detector"] == "local-python-mediapipe"
    assert result["width"] == 320 and result["height"] == 240
    assert len(result["poseLandmarks"]) == 33
    assert len(result["hands"]) == 1 and len(result["hands"][0]) == 21
    assert result["handLabels"] == ["Left"]
    assert result["latencyMs"] >= 0
