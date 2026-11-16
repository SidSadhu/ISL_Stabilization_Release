from __future__ import annotations

import json
import uuid

import pytest

from isl_recognition.iot_protocol import (
    SimulatedESP32,
    build_command,
    load_label_commands,
    validate_command,
)


LABELS = {"hello": 22, "time": 43, "quiet": 34}
MAPPINGS = {"hello": "HELLO_ACTION", "time": "TIME_ACTION"}


def make_command(label: str = "hello", confidence: float = 0.91, now: float = 1_800_000_000.0):
    return build_command(
        capture_id=str(uuid.uuid4()),
        source_label=label,
        confidence=confidence,
        label_commands=MAPPINGS,
        now=now,
        ttl_seconds=10,
        min_confidence=0.70,
    )


def test_load_selected_esp8266_mappings(tmp_path):
    path = tmp_path / "iot_label_map.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "mappings": MAPPINGS,
    }), encoding="utf-8")

    assert load_label_commands(path, LABELS) == MAPPINGS


def test_quiet_remains_unmapped():
    with pytest.raises(ValueError, match="no configured IoT mapping"):
        build_command(
            capture_id=str(uuid.uuid4()),
            source_label="quiet",
            confidence=0.99,
            label_commands=MAPPINGS,
        )


@pytest.mark.parametrize("label,expected", [
    ("hello", "HELLO_ACTION"),
    ("time", "TIME_ACTION"),
])
def test_build_and_validate_esp8266_commands(label, expected):
    now = 1_800_000_000.0
    command = make_command(label=label, now=now)

    assert command["command"] == expected
    assert command["source_label"] == label
    assert validate_command(
        command, label_commands=MAPPINGS, now=now + 1, min_confidence=0.70
    ) == (True, "validated")


def test_low_confidence_command_cannot_be_built():
    with pytest.raises(ValueError, match="Confidence"):
        make_command(label="hello", confidence=0.69)


def test_simulator_acknowledges_hello_and_time_once():
    for label, command_name in (
        ("hello", "HELLO_ACTION"),
        ("time", "TIME_ACTION"),
    ):
        command = make_command(label=label)
        simulator = SimulatedESP32(MAPPINGS, min_confidence=0.70)

        first = simulator.process(command, now=command["issued_at"] + 1)
        second = simulator.process(command, now=command["issued_at"] + 1)

        assert first["status"] == "acknowledged"
        assert second["status"] == "duplicate"
        assert simulator.actions == [command_name]


def test_expired_device_command_is_rejected_by_protocol():
    command = make_command()
    ok, reason = validate_command(
        command,
        label_commands=MAPPINGS,
        now=command["expires_at"],
        min_confidence=0.70,
    )
    assert not ok
    assert reason == "command_expired"


def test_command_schema_remains_version_1_0():
    command = make_command()
    assert command["schema_version"] == "1.0"
    assert set(command) == {
        "schema_version", "command_id", "command", "source_label",
        "confidence", "capture_id", "issued_at", "expires_at",
    }


def test_legacy_led_blink_simulator_command_remains_supported():
    legacy_mapping = {"hello": "LED_BLINK"}
    command = build_command(
        capture_id=str(uuid.uuid4()),
        source_label="hello",
        confidence=0.95,
        label_commands=legacy_mapping,
        now=1_800_000_000.0,
    )
    simulator = SimulatedESP32(legacy_mapping)
    result = simulator.process(command, now=1_800_000_001.0)

    assert result["status"] == "acknowledged"
    assert simulator.led_blinks == 1
    assert simulator.actions == []
