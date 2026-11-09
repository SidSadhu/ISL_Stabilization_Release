"""
test_confidence_policy.py — Verify confidence threshold policy and UI separation.

Key requirements:
  1. Default threshold is 0.40 (provisional, uncalibrated).
  2. Rejected predictions always expose candidate + reason; never silently pass.
  3. Top-5 candidates are always returned in result, regardless of outcome.
  4. threshold_is_calibrated=False is always asserted in API response.
  5. Accepted prediction with score >= 0.40 has outcome='accepted'.
  6. Rejected prediction with score < 0.40 has outcome='rejected' with reason.
  7. IoT is never triggered on rejected prediction.
"""
import base64
import time
from dataclasses import replace

import numpy as np
from fastapi.testclient import TestClient

from isl_recognition.app_config import Settings
from isl_recognition.webapp import create_app
import isl_recognition.webapp as webapp_module
from test_webapp import FakeModel, FakeMQTT, make_services, send_valid_frames


def test_default_threshold_is_040():
    s = Settings()
    assert abs(s.confidence_threshold - 0.40) < 1e-6, (
        f"Expected 0.40 default threshold, got {s.confidence_threshold}"
    )


def test_accepted_prediction_at_threshold(tmp_path):
    """Score exactly at threshold → accepted."""
    model = FakeModel(index=22, confidence=0.40)
    svc = make_services(tmp_path, model=model)
    svc.settings = replace(svc.settings, confidence_threshold=0.40)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'}); ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    assert msg['result']['outcome'] == 'accepted'
    assert msg['result']['confidence'] == 0.40


def test_rejected_prediction_exposes_candidate_and_reason(tmp_path):
    """Score below threshold → rejected; candidate label and reason still visible."""
    model = FakeModel(index=22, confidence=0.35)
    svc = make_services(tmp_path, model=model)
    svc.settings = replace(svc.settings, confidence_threshold=0.40)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'}); ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    r = msg['result']
    assert r['outcome'] == 'rejected'
    # Candidate must still be exposed
    assert r['predicted_label'] == 'hello'
    assert abs(r['confidence'] - 0.35) < 1e-6
    # Reason must mention threshold
    assert r['reason'] is not None and '0.40' in r['reason']
    # top5_predictions must be present
    assert isinstance(r.get('top5_predictions'), list)
    # threshold_is_calibrated must be False
    assert r.get('threshold_is_calibrated') is False


def test_rejected_prediction_never_triggers_iot(tmp_path, monkeypatch):
    """Rejected prediction must not publish any IoT command."""
    model = FakeModel(index=22, confidence=0.35)
    svc = make_services(tmp_path, model=model)
    svc.settings = replace(svc.settings, confidence_threshold=0.40, pre_iot_lockdown=False)
    svc.iot_enabled = True
    svc.mqtt = FakeMQTT()

    # Keep this policy test independent of Windows wall-clock scheduling jitter.
    # Only this webapp module receives a synthetic monotonic clock.
    # Production max_capture_seconds remains unchanged at 6 seconds.
    class _SyntheticCaptureClock:
        def __init__(self, real_time):
            self._real_time = real_time
            self._tick = 0.0

        def monotonic(self):
            self._tick += 0.001
            return self._tick

        def __getattr__(self, name):
            return getattr(self._real_time, name)

    monkeypatch.setattr(
        webapp_module,
        "time",
        _SyntheticCaptureClock(webapp_module.time),
    )

    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'}); ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    assert msg['result']['outcome'] == 'rejected'
    assert svc.mqtt.commands == []
    assert msg['iot']['status'] in ('rejected', 'disabled', 'suppressed')


def test_health_reports_uncalibrated(tmp_path):
    svc = make_services(tmp_path)
    with TestClient(create_app(svc)) as client:
        h = client.get('/api/health').json()
    assert h['confidence_threshold_is_calibrated'] is False


def test_top5_always_present_on_accepted(tmp_path):
    model = FakeModel(index=22, confidence=0.91)
    svc = make_services(tmp_path, model=model)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'}); ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    r = msg['result']
    assert r['outcome'] == 'accepted'
    assert isinstance(r.get('top5_predictions'), list)
    assert len(r['top5_predictions']) >= 1
    assert r['top5_predictions'][0]['label'] == 'hello'
