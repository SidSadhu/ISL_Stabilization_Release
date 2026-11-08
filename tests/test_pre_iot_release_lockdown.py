from dataclasses import replace
import base64
import time

from fastapi.testclient import TestClient

from isl_recognition.app_config import Settings
from isl_recognition.webapp import create_app
from test_webapp import FakeModel, FakeMQTT, make_services, send_valid_frames


def test_pre_iot_lockdown_is_reported_and_blocks_enable_endpoint(tmp_path):
    svc = make_services(tmp_path, model=FakeModel())
    svc.settings = replace(svc.settings, pre_iot_lockdown=True)
    svc.mqtt = FakeMQTT()
    with TestClient(create_app(svc)) as client:
        health = client.get('/api/health').json()
        assert health['pre_iot_lockdown'] is True
        assert health['iot_enabled'] is False
        response = client.post('/api/iot/enable', json={'enabled': True}).json()
        assert response['iot_enabled'] is False
        assert response['pre_iot_lockdown'] is True
        assert svc.iot_enabled is False
        assert svc.mqtt.commands == []


def test_pre_iot_lockdown_suppresses_command_even_if_runtime_flag_is_manually_set(tmp_path):
    svc = make_services(tmp_path, model=FakeModel())
    svc.settings = replace(svc.settings, pre_iot_lockdown=True)
    svc.mqtt = FakeMQTT()
    # Simulate accidental internal state mutation; command publisher must still refuse.
    svc.iot_enabled = True
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            assert ws.receive_json()['type'] == 'connected'
            ws.send_json({'type': 'start'})
            assert ws.receive_json()['type'] == 'capture_started'
            send_valid_frames(ws, 14)
            ws.send_json({'type': 'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result':
                    break
        assert msg['result']['outcome'] == 'accepted'
        assert msg['iot']['status'] == 'suppressed'
        assert svc.mqtt.commands == []
        assert client.get('/api/iot/history').json()['events'] == []
