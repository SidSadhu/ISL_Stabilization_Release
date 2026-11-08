from dataclasses import replace
from pathlib import Path
import base64
import json
import sys
import time
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isl_recognition.app_config import Settings
from isl_recognition.labels import load_label_map
from isl_recognition.storage import EventStore
from isl_recognition.webapp import RuntimeServices, build_services, create_app




TEST_IOT_MAPPINGS = {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}

def configure_test_iot(svc):
    # Test-only fixture. These names are not the product's final chosen IoT signs.
    svc.iot_label_commands = dict(TEST_IOT_MAPPINGS)
    return svc


class FakeExtractor:
    available = True
    def __init__(self): self.sessions = 0; self.extracted = 0; self.inputs = []
    def start_session(self): self.sessions += 1; return object()
    def extract(self, jpeg, session):
        self.extracted += 1; self.inputs.append(jpeg)
        return SimpleNamespace(features=np.ones((134,), dtype=np.float32), pose_found=True, hand_count=1)
    def close_session(self, session): pass


class FakeModel:
    device = "cpu"
    def __init__(self, index=22, confidence=.91): self.calls = 0; self.index = index; self.confidence = confidence
    def predict(self, padded):
        self.calls += 1
        assert padded.shape == (169,134) and padded.dtype == np.float32 and np.isfinite(padded).all()
        return {"predicted_index":self.index,"confidence":self.confidence,"top5_indices":[self.index],"top5_probabilities":[self.confidence],"device":"cpu"}


class FakeMQTT:
    def __init__(self): self.commands=[]
    def publish_command(self, payload): self.commands.append(payload); return True, "fake publish accepted"
    def close(self): pass


def make_services(tmp_path, model=None, extractor=None, labels=None):
    settings = replace(Settings(), database_path=tmp_path / "events.sqlite3", min_capture_frames=12, queue_size=4, iot_cooldown_seconds=0, pre_iot_lockdown=False)
    settings.validate()
    labels = labels or load_label_map(ROOT / "config" / "label_map_include50.json")
    return RuntimeServices(settings=settings, store=EventStore(settings.database_path), model=model,
                           labels=labels, extractor=extractor or FakeExtractor(), model_error=None if model else "test model unavailable")


def send_valid_frames(ws, count=14):
    # WebSocket frames require strictly increasing timestamps. Use a deterministic
    # synthetic clock because a tight test loop can call time.time() multiple times
    # within the same clock tick on Windows.
    first_timestamp = time.time()
    encoded = base64.b64encode(b"fake-jpeg").decode()
    for index in range(count):
        ws.send_json({"type": "frame", "timestamp": first_timestamp + index * 0.001, "jpeg_b64": encoded})
        msg = ws.receive_json()
        assert msg["type"] == "progress", msg


def test_health_and_missing_model_are_reported_without_fake_prediction(tmp_path):
    app = create_app(make_services(tmp_path, model=None))
    with TestClient(app) as client:
        health=client.get('/api/health').json()
        assert health["status"] == "degraded" and not health["model_ready"]
        with client.websocket_connect('/ws/capture') as ws:
            assert ws.receive_json()["type"] == "connected"
            ws.send_json({"type":"start"})
            error=ws.receive_json()
            assert error["code"] == "model_unavailable"
        assert client.get('/api/history').json()["events"] == []


def test_valid_capture_runs_exactly_one_inference_and_persists_history(tmp_path):
    model=FakeModel(); svc=make_services(tmp_path, model=model)
    app=create_app(svc)
    with TestClient(app) as client:
        with client.websocket_connect('/ws/capture') as ws:
            assert ws.receive_json()["type"] == "connected"
            ws.send_json({"type":"start"}); assert ws.receive_json()["type"] == "capture_started"
            send_valid_frames(ws, 14)
            ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"] == "result": break
            assert msg["result"]["predicted_label"] == "hello"
            assert msg["result"]["outcome"] == "accepted"
            assert msg["iot"]["status"] == "disabled"
        assert model.calls == 1
        history=client.get('/api/history').json()["events"]
        assert len(history)==1 and history[0]["predicted_label"] == "hello"
        assert client.get('/api/iot/history').json()["events"] == []


def test_invalid_short_capture_is_not_inferred(tmp_path):
    model=FakeModel(); svc=make_services(tmp_path, model=model)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json()
            ws.send_json({"type":"frame","timestamp":time.time(),"jpeg_b64":base64.b64encode(b"x").decode()})
            assert ws.receive_json()["type"] == "progress"
            ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"]=="result": break
        assert msg["result"]["outcome"] == "invalid_capture"
        assert model.calls == 0
        assert client.get('/api/history').json()["events"][0]["outcome"] == "invalid_capture"


def test_iot_enable_is_explicit_and_mapped_hello_creates_command(tmp_path):
    model=FakeModel(); svc=configure_test_iot(make_services(tmp_path, model=model)); svc.mqtt=FakeMQTT()
    with TestClient(create_app(svc)) as client:
        assert client.get('/api/health').json()["iot_enabled"] is False
        assert client.post('/api/iot/enable',json={"enabled":True}).json()["iot_enabled"] is True
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json(); send_valid_frames(ws,14); ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"]=="result": break
        assert msg["iot"]["status"] == "pending"
        assert len(svc.mqtt.commands)==1 and svc.mqtt.commands[0]["command"] == "LED_BLINK"
        assert client.get('/api/iot/history').json()["events"][0]["publish_status"] == "published"


def test_mapped_thankyou_prediction_generates_triple_blink_command(tmp_path):
    model=FakeModel(index=42, confidence=.95); svc=configure_test_iot(make_services(tmp_path, model=model)); svc.mqtt=FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable',json={"enabled":True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json(); send_valid_frames(ws,14); ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"] == "result": break
        assert msg["result"]["predicted_label"] == "thankyou"
        assert msg["result"]["outcome"] == "accepted"
        assert msg["iot"]["status"] == "pending"
        assert len(svc.mqtt.commands) == 1
        assert svc.mqtt.commands[0]["source_label"] == "thankyou"
        assert svc.mqtt.commands[0]["command"] == "LED_BLINK_THREE"


def test_bad_websocket_json_and_unknown_message_are_reported(tmp_path):
    svc=make_services(tmp_path,model=FakeModel())
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json()
            ws.send_text('{bad-json'); assert ws.receive_json()["code"] == "invalid_json"
            ws.send_json({"type":"surprise"}); assert ws.receive_json()["code"] == "unknown_message_type"


def test_history_reset_requires_confirmation_and_never_runs_command(tmp_path):
    svc=make_services(tmp_path,model=FakeModel())
    with TestClient(create_app(svc)) as client:
        assert client.post('/api/history/reset',json={"confirm":False}).status_code == 400
        assert client.post('/api/history/reset',json={"confirm":True}).json()=={"recognition_deleted":0,"iot_deleted":0}


def test_low_confidence_prediction_never_publishes_iot(tmp_path):
    model=FakeModel(confidence=.39); svc=configure_test_iot(make_services(tmp_path, model=model)); svc.mqtt=FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable',json={"enabled":True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json(); send_valid_frames(ws,14); ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"]=="result": break
        assert msg["result"]["outcome"] == "rejected"
        assert msg["iot"]["status"] == "rejected"
        assert svc.mqtt.commands == []
        assert client.get('/api/iot/history').json()["events"] == []


def test_unmapped_accepted_label_never_publishes_iot(tmp_path):
    model=FakeModel(index=8,confidence=.99); svc=configure_test_iot(make_services(tmp_path,model=model)); svc.mqtt=FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable',json={"enabled":True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json(); send_valid_frames(ws,14); ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"]=="result": break
        assert msg["result"]["predicted_label"] == "court"
        assert msg["result"]["outcome"] == "accepted"
        assert msg["iot"]["status"] == "not_mapped"
        assert svc.mqtt.commands == []


def test_model_exception_is_persisted_and_cannot_trigger_iot(tmp_path):
    class BrokenModel(FakeModel):
        def predict(self, padded):
            self.calls += 1
            raise RuntimeError('test inference failure')
    model=BrokenModel(); svc=make_services(tmp_path,model=model); svc.mqtt=FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable',json={"enabled":True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json(); send_valid_frames(ws,14); ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"]=="result": break
        assert msg["result"]["outcome"] == "error"
        assert svc.mqtt.commands == []
        assert client.get('/api/iot/history').json()["events"] == []
        assert client.get('/api/history').json()["events"][0]["reason"].startswith('Inference/preprocessing error')


def test_capture_frame_limit_is_exactly_169_and_visible(tmp_path):
    model=FakeModel(); svc=make_services(tmp_path,model=model)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json()
            first_timestamp = time.time()
            encoded = base64.b64encode(b"x").decode()
            for i in range(169):
                ws.send_json({"type": "frame", "timestamp": first_timestamp + i * 0.001, "jpeg_b64": encoded})
                first=ws.receive_json()
                assert first["type"] == "progress"
                if i == 168:
                    second=ws.receive_json()
                    assert second["type"] == "frame_limit_reached"
            ws.send_json({"type":"frame","timestamp":first_timestamp + 169 * 0.001,"jpeg_b64":encoded})
            assert ws.receive_json()["type"] == "frame_limit_reached"
            ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"] == "result": break
        assert msg["result"]["frame_count"] == 169
        assert model.calls == 1
        assert msg["result"]["mediapipe_latency_ms"] is not None
        assert msg["result"]["inference_latency_ms"] is not None
        assert msg["result"]["end_to_end_latency_ms"] is not None


def test_retention_endpoint_requires_timestamp_and_confirmation(tmp_path):
    svc=make_services(tmp_path,model=FakeModel())
    with TestClient(create_app(svc)) as client:
        assert client.post('/api/history/retention',json={"before":"2026-01-01T00:00:00Z","confirm":False}).status_code == 400
        assert client.post('/api/history/retention',json={"before":"not-a-time","confirm":True}).status_code == 400


def test_stop_drains_frames_already_queued_before_single_inference(tmp_path):
    model = FakeModel()
    svc = make_services(tmp_path, model=model)
    svc.settings = replace(svc.settings, queue_size=32)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json()
            # Deliberately send without waiting for progress to exercise queued-frame draining.
            first_timestamp = time.time()
            encoded = base64.b64encode(b"fake-jpeg").decode()
            for index in range(14):
                ws.send_json({"type": "frame", "timestamp": first_timestamp + index * 0.001, "jpeg_b64": encoded})
            ws.send_json({"type":"stop"})
            progress = 0
            while True:
                msg = ws.receive_json()
                if msg["type"] == "progress":
                    progress += 1
                if msg["type"] == "result":
                    break
        assert progress == 14
        assert msg["result"]["frame_count"] == 14
        assert msg["result"]["outcome"] == "accepted"
        assert model.calls == 1


def test_cancel_discards_capture_without_inference_or_iot(tmp_path):
    model = FakeModel()
    svc = make_services(tmp_path, model=model)
    svc.mqtt = FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable', json={"enabled":True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json()
            ws.send_json({"type":"frame","timestamp":time.time(),"jpeg_b64":base64.b64encode(b"fake-jpeg").decode()})
            assert ws.receive_json()["type"] == "progress"
            ws.send_json({"type":"cancel"})
            while True:
                msg = ws.receive_json()
                if msg["type"] == "cancelled":
                    break
        assert model.calls == 0
        assert svc.mqtt.commands == []
        assert client.get('/api/history').json()["events"][0]["outcome"] == "cancelled"


def test_mqtt_publish_failure_is_recorded_but_recognition_survives(tmp_path):
    class FailedMQTT(FakeMQTT):
        def publish_command(self, payload):
            self.commands.append(payload)
            return False, "broker is unavailable"
    model=FakeModel(); svc=configure_test_iot(make_services(tmp_path, model=model)); svc.mqtt=FailedMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable',json={"enabled":True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json(); send_valid_frames(ws,14); ws.send_json({"type":"stop"})
            while True:
                msg=ws.receive_json()
                if msg["type"]=="result": break
        assert msg["result"]["outcome"] == "accepted"
        assert msg["iot"]["status"] == "publish_failed"
        assert client.get('/api/history').json()["events"][0]["outcome"] == "accepted"
        iot_event=client.get('/api/iot/history').json()["events"][0]
        assert iot_event["publish_status"] == "failed"
        assert iot_event["ack_status"] == "publish_failed"


def test_non_monotonic_frame_timestamp_is_rejected(tmp_path):
    svc=make_services(tmp_path, model=FakeModel())
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); ws.receive_json()
            ts=time.time()
            encoded=base64.b64encode(b"fake-jpeg").decode()
            ws.send_json({"type":"frame","timestamp":ts,"jpeg_b64":encoded})
            assert ws.receive_json()["type"] == "progress"
            ws.send_json({"type":"frame","timestamp":ts,"jpeg_b64":encoded})
            error=ws.receive_json()
            assert error["code"] == "non_monotonic_frame_timestamp"
            ws.send_json({"type":"cancel"})
            while ws.receive_json()["type"] != "cancelled":
                pass
    assert svc.store.recognition_history()[0]["frame_count"] == 0
    assert svc.store.recognition_history()[0]["received_frames"] == 1


def test_vocabulary_endpoint_returns_official_classes(tmp_path):
    svc = make_services(tmp_path, model=FakeModel())
    with TestClient(create_app(svc)) as client:
        response = client.get("/api/vocabulary")
        assert response.status_code == 200
        body = response.json()
        assert body["vocabulary_name"] == "AI4Bharat INCLUDE-50"
        assert body["class_count"] == 50
        assert len(body["classes"]) == 50
        assert body["classes"][3]["label"] == "black"
        assert body["classes"][3]["index"] == 3
        assert body["classes"][22]["label"] == "hello"


def test_reference_gallery_lists_official_classes_and_only_serves_local_video(tmp_path):
    video_root = tmp_path / "videos"
    video_file = video_root / "Greetings" / "22. Hello" / "MVI_hello.mp4"
    video_file.parent.mkdir(parents=True)
    video_file.write_bytes(b"test-video-bytes")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"videos": [
        {"parent_label": "Greetings", "label": "Greetings/22. Hello", "video_path": "Greetings/22. Hello/MVI_hello.mp4", "include_50": True},
        {"parent_label": "Greetings", "label": "Greetings/55. Thank you", "video_path": "Greetings/55. Thank you/MVI_thanks.mp4", "include_50": True},
        {"parent_label": "Colours", "label": "Colours/50. Yellow", "video_path": "Colours/50. Yellow/MVI_yellow.mp4", "include_50": False},
        # Some viewer rows may have include_50=false but still match one of the 50 model glosses.
        {"parent_label": "Places", "label": "Places/23. Court", "video_path": "Places/23. Court/MVI_court.MOV", "include_50": False, "model_class_match": True, "matched_model_label": "court"},
    ]}), encoding="utf-8")
    base = Settings()
    settings = replace(base, database_path=tmp_path / "events.sqlite3", reference_video_dir=video_root, reference_metadata_path=metadata_path)
    svc = RuntimeServices(settings=settings, store=EventStore(settings.database_path), labels=load_label_map(ROOT / "config" / "label_map_include50.json"), extractor=FakeExtractor())
    with TestClient(create_app(svc)) as client:
        gallery = client.get("/api/reference-videos").json()
        assert gallery["metadata_ready"] is True
        assert gallery["class_count"] == 50
        assert gallery["available_video_count"] == 1
        hello = next(item for item in gallery["classes"] if item["label"] == "hello")
        assert hello["available_count"] == 1
        court = next(item for item in gallery["classes"] if item["label"] == "court")
        assert court["video_count"] == 1
        assert court["available_count"] == 0
        assert client.get(hello["videos"][0]["url"]).content == b"test-video-bytes"
        assert client.get("/api/reference-video", params={"path": "../secret.mp4"}).status_code == 400
        assert client.get("/api/reference-video", params={"path": "missing.mp4"}).status_code == 404


def test_capture_buffers_source_frames_then_extracts_every_frame_after_stop(tmp_path):
    model = FakeModel(); extractor = FakeExtractor(); svc = make_services(tmp_path, model=model, extractor=extractor)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({"type":"start"}); started = ws.receive_json()
            assert started["type"] == "capture_started" and started["max_capture_seconds"] == svc.settings.max_capture_seconds
            ts = time.time(); encoded_frames = [base64.b64encode(f"jpeg-{i}".encode()).decode() for i in range(14)]
            for i, encoded in enumerate(encoded_frames):
                ws.send_json({"type":"frame", "timestamp":ts + i * 0.001, "jpeg_b64":encoded})
                progress = ws.receive_json(); assert progress["type"] == "progress" and progress["phase"] == "recording"
            # Critical regression check: no landmark extraction should run during capture.
            assert extractor.extracted == 0
            ws.send_json({"type":"stop"})
            seen = []
            while True:
                msg = ws.receive_json(); seen.append(msg["type"])
                if msg["type"] == "result":
                    result = msg["result"]; break
        assert "processing_started" in seen and "processing_progress" in seen
        assert extractor.extracted == 14
        assert result["received_frames"] == 14 and result["frame_count"] == 14
        assert result["frames_dropped"] == 0 and result["outcome"] == "accepted"


def test_normal_result_reports_per_hand_coverage_without_changing_contract(tmp_path):
    model = FakeModel(); svc = make_services(tmp_path, model=model)
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            send_valid_frames(ws, 14); ws.send_json({'type':'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    result = msg['result']
    assert result['pose_ratio'] == 1.0
    assert result['hand_ratio'] == 1.0
    assert result['hand1_ratio'] == 1.0
    assert result['hand2_ratio'] == 0.0
    assert result['both_hand_ratio'] == 0.0
    assert result['diagnostic_mode'] is False
    assert result['diagnostic_override_used'] is False
    assert result['mediapipe_p95_latency_ms'] is not None
    assert model.calls == 1


def test_diagnostic_mode_can_override_low_nonzero_hand_coverage_but_never_issue_iot(tmp_path):
    class IntermittentHandExtractor(FakeExtractor):
        def extract(self, jpeg, session):
            self.extracted += 1; self.inputs.append(jpeg)
            found = self.extracted <= 3
            return SimpleNamespace(
                features=np.ones((134,), dtype=np.float32), pose_found=True,
                hand_count=1 if found else 0, hand1_found=found, hand2_found=False,
            )

    model = FakeModel(confidence=.95)
    svc = make_services(tmp_path, model=model, extractor=IntermittentHandExtractor())
    svc.mqtt = FakeMQTT()
    with TestClient(create_app(svc)) as client:
        client.post('/api/iot/enable', json={'enabled': True})
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            send_valid_frames(ws, 14)
            ws.send_json({'type':'stop', 'diagnostic_mode':True})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    result = msg['result']
    assert result['outcome'] == 'accepted'
    assert result['hand_ratio'] < .5
    assert result['diagnostic_mode'] is True
    assert result['diagnostic_override_used'] is True
    assert msg['iot']['status'] == 'suppressed'
    assert 'suppressed' in msg['iot']['message'].lower()
    assert svc.mqtt.commands == []


def test_low_hand_coverage_without_diagnostic_mode_still_rejects_before_inference(tmp_path):
    class IntermittentHandExtractor(FakeExtractor):
        def extract(self, jpeg, session):
            self.extracted += 1; self.inputs.append(jpeg)
            found = self.extracted <= 3
            return SimpleNamespace(
                features=np.ones((134,), dtype=np.float32), pose_found=True,
                hand_count=1 if found else 0, hand1_found=found, hand2_found=False,
            )

    model = FakeModel()
    svc = make_services(tmp_path, model=model, extractor=IntermittentHandExtractor())
    with TestClient(create_app(svc)) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            send_valid_frames(ws, 14); ws.send_json({'type':'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    assert msg['result']['outcome'] == 'invalid_capture'
    assert msg['result']['hand_ratio'] < .5
    assert msg['result']['diagnostic_mode'] is False
    assert model.calls == 0


def test_iot_cannot_be_enabled_without_exactly_two_mappings(tmp_path):
    svc = make_services(tmp_path, model=FakeModel())
    with TestClient(create_app(svc)) as client:
        health = client.get('/api/health').json()
        assert health['iot_mapping_configured'] is False
        assert health['iot_mapping_count'] == 0
        response = client.post('/api/iot/enable', json={'enabled': True}).json()
        assert response['iot_enabled'] is False
        assert 'exactly two' in response['message']


def test_recognised_text_can_be_accepted_but_iot_has_separate_070_gate(tmp_path):
    model = FakeModel(index=22, confidence=.60)
    svc = configure_test_iot(make_services(tmp_path, model=model))
    svc.mqtt = FakeMQTT()
    with TestClient(create_app(svc)) as client:
        enabled = client.post('/api/iot/enable', json={'enabled': True}).json()
        assert enabled['iot_enabled'] is True
        with client.websocket_connect('/ws/capture') as ws:
            ws.receive_json(); ws.send_json({'type':'start'}); ws.receive_json()
            send_valid_frames(ws, 14); ws.send_json({'type':'stop'})
            while True:
                msg = ws.receive_json()
                if msg['type'] == 'result': break
    assert msg['result']['outcome'] == 'accepted'
    assert msg['iot']['status'] == 'rejected'
    assert '0.70' in msg['iot']['message']
    assert svc.mqtt.commands == []


def test_build_services_loads_two_iot_labels_from_external_config(tmp_path):
    mapping_path = tmp_path / 'iot_label_map.json'
    mapping_path.write_text(json.dumps({
        'schema_version': '1.0',
        'mappings': {'hello': 'LED_BLINK', 'thankyou': 'LED_BLINK_THREE'},
    }), encoding='utf-8')
    settings = replace(Settings(), database_path=tmp_path / 'events.sqlite3', iot_label_map_path=mapping_path)
    svc = build_services(settings)
    assert svc.iot_label_commands == {'hello': 'LED_BLINK', 'thankyou': 'LED_BLINK_THREE'}
    assert svc.iot_mapping_error is None


def test_build_services_fails_closed_for_invalid_iot_config(tmp_path):
    mapping_path = tmp_path / 'iot_label_map.json'
    mapping_path.write_text(json.dumps({
        'schema_version': '1.0', 'mappings': {'hello': 'LED_BLINK'},
    }), encoding='utf-8')
    settings = replace(Settings(), database_path=tmp_path / 'events.sqlite3', iot_label_map_path=mapping_path)
    svc = build_services(settings)
    assert svc.iot_label_commands == {}
    assert svc.iot_mapping_error and 'exactly two' in svc.iot_mapping_error


def test_iot_capture_cannot_be_reissued_after_history_reset(tmp_path):
    import asyncio
    from isl_recognition.webapp import maybe_issue_iot_command

    svc = configure_test_iot(make_services(tmp_path, model=FakeModel()))
    svc.iot_enabled = True
    svc.mqtt = FakeMQTT()
    capture_id = "capture-replay-integration"
    recognition = {
        "capture_id": capture_id, "outcome": "accepted", "predicted_label": "hello",
        "confidence": .95, "frame_count": 20,
    }
    svc.store.add_recognition(recognition)
    result = {**recognition, "status": "accepted"}

    first = asyncio.run(maybe_issue_iot_command(svc, result))
    assert first["status"] == "pending"
    assert len(svc.mqtt.commands) == 1

    # The user may clear visible history, but this is not permission to replay
    # the same capture and actuate the device again.
    svc.store.reset_history()
    svc.store.add_recognition(recognition)
    retry = asyncio.run(maybe_issue_iot_command(svc, result))
    assert retry["status"] == "duplicate_capture"
    assert len(svc.mqtt.commands) == 1
