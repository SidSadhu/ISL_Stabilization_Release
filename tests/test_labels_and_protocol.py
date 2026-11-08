from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isl_recognition.iot_protocol import SimulatedESP32, build_command, validate_command, load_label_commands
from isl_recognition.labels import invert_label_map, load_label_map


def test_bundled_official_label_map_has_stable_50_class_order():
    labels = load_label_map(ROOT / "config" / "label_map_include50.json")
    assert len(labels) == 50
    assert labels["court"] == 8
    assert labels["hello"] == 22
    assert [invert_label_map(labels)[i] for i in range(50)][8] == "court"


def test_safe_iot_command_requires_explicit_allowlist():
    mapping = {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}
    command = build_command(capture_id="cap-test", source_label="hello", confidence=.91,
                            label_commands=mapping, now=1800000000)
    ok, reason = validate_command(command, label_commands=mapping, now=1800000001)
    assert ok and reason == "validated"
    with pytest.raises(ValueError):
        build_command(capture_id="cap-test", source_label="court", confidence=.99,
                      label_commands=mapping, now=1800000000)
    with pytest.raises(ValueError):
        build_command(capture_id="cap-test", source_label="hello", confidence=.69,
                      label_commands=mapping, now=1800000000)


def test_iot_simulator_accepts_once_and_deduplicates():
    mapping = {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}
    simulator = SimulatedESP32(mapping)
    command = build_command(capture_id="cap-1", source_label="hello", confidence=.9, label_commands=mapping, now=1800000000)
    accepted = simulator.process(command, now=1800000001)
    duplicate = simulator.process(command, now=1800000001)
    assert accepted["status"] == "acknowledged"
    assert duplicate["status"] == "duplicate"
    assert simulator.led_blinks == 1


def test_iot_simulator_rejects_bad_command_and_expired_payload():
    mapping = {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}
    simulator = SimulatedESP32(mapping)
    command = build_command(capture_id="cap-2", source_label="hello", confidence=.9, label_commands=mapping, now=1800000000)
    command["command"] = "LED_ON"
    assert simulator.process(command, now=1800000001)["status"] == "rejected"
    expired = build_command(capture_id="cap-3", source_label="hello", confidence=.9, label_commands=mapping, now=1800000000)
    assert simulator.process(expired, now=1800000011)["detail"] == "command_expired"
    assert simulator.led_blinks == 0


def test_iot_simulator_rejects_low_confidence_and_non_object():
    mapping = {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}
    simulator = SimulatedESP32(mapping)
    low = build_command(capture_id="cap-4", source_label="hello", confidence=.71, label_commands=mapping, now=1800000000)
    low["confidence"] = .40
    assert simulator.process(low, now=1800000001)["detail"] == "confidence_out_of_range"
    assert simulator.process("not-json", now=1800000001)["status"] == "rejected"


def test_custom_map_cannot_silently_reorder_official_classes(tmp_path):
    import json
    labels = load_label_map(ROOT / "config" / "label_map_include50.json")
    labels["hello"], labels["thankyou"] = labels["thankyou"], labels["hello"]
    path = tmp_path / "reordered.json"
    path.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from the bundled official"):
        load_label_map(path)


def test_iot_mapping_config_requires_zero_or_two_valid_classes(tmp_path):
    import json
    labels = load_label_map(ROOT / "config" / "label_map_include50.json")
    path = tmp_path / "iot_label_map.json"
    path.write_text(json.dumps({"schema_version": "1.0", "mappings": {}}), encoding="utf-8")
    assert load_label_commands(path, labels) == {}
    path.write_text(json.dumps({"schema_version": "1.0", "mappings": {
        "hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}}), encoding="utf-8")
    assert load_label_commands(path, labels) == {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}
    for mappings in ({"hello": "LED_BLINK"}, {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE", "good": "LED_BLINK"},
                     {"not_an_official_label": "LED_BLINK", "thankyou": "LED_BLINK_THREE"},
                     {"hello": "LED_ON", "thankyou": "LED_BLINK_THREE"}):
        path.write_text(json.dumps({"schema_version": "1.0", "mappings": mappings}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_label_commands(path, labels)


def test_firmware_defaults_have_empty_allowlist_and_gpio_disabled():
    firmware = (ROOT / 'esp32' / 'isl_mqtt_endpoint.ino').read_text(encoding='utf-8')
    assert '#define LED_GPIO -1' in firmware
    assert '#define ISL_IOT_LABEL_1 ""' in firmware
    assert '#define ISL_IOT_LABEL_2 ""' in firmware
    assert 'approvedSourceCommandPair' in firmware
    assert 'source_command_pair_not_allowlisted_or_unconfigured' in firmware


def test_simulator_deduplicates_capture_even_when_command_id_changes():
    mapping = {"hello": "LED_BLINK", "thankyou": "LED_BLINK_THREE"}
    simulator = SimulatedESP32(mapping)
    first = build_command(
        capture_id="same-capture", source_label="hello", confidence=.95,
        label_commands=mapping, now=1800000000,
    )
    second = build_command(
        capture_id="same-capture", source_label="hello", confidence=.96,
        label_commands=mapping, now=1800000000,
    )
    assert first["command_id"] != second["command_id"]
    assert simulator.process(first, now=1800000001)["status"] == "acknowledged"
    duplicate = simulator.process(second, now=1800000001)
    assert duplicate["status"] == "duplicate"
    assert duplicate["detail"] == "capture_id_already_processed"
    assert simulator.led_blinks == 1


def test_firmware_persists_and_checks_capture_level_deduplication():
    firmware = (ROOT / "esp32" / "isl_mqtt_endpoint.ino").read_text(encoding="utf-8")
    assert "seenCaptureIds" in firmware
    assert "captureSeenBefore" in firmware
    assert 'String("cap")' in firmware
    assert '"capture_id_already_processed"' in firmware


def test_firmware_clamps_persisted_dedup_ring_pointer():
    firmware = (ROOT / "esp32" / "isl_mqtt_endpoint.ino").read_text(encoding="utf-8")
    assert "persistedNext >= 0 && persistedNext < MAX_SEEN_IDS" in firmware
    assert 'if (seenNext != persistedNext) prefs.putInt("next", seenNext);' in firmware
