from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from dataclasses import replace
from pathlib import Path

from isl_recognition.app_config import Settings
from isl_recognition.iot_protocol import (
    SimulatedESP32,
    build_command,
    expected_sign_matches,
    load_label_commands,
    validate_command,
)
from isl_recognition.webapp import maybe_issue_iot_command


LABELS = {"hello": 22, "time": 43, "quiet": 34}
MAPPINGS = {"quiet": "QUIET_ACTION", "time": "TIME_ACTION"}


def make_command(label: str = "quiet", confidence: float = 0.91, now: float = 1_800_000_000.0):
    return build_command(
        capture_id=str(uuid.uuid4()),
        source_label=label,
        confidence=confidence,
        label_commands=MAPPINGS,
        now=now,
        ttl_seconds=10,
        min_confidence=0.30,
    )


@pytest.mark.parametrize("predicted,expected,matched", [
    ("quiet", "quiet", True),
    ("time", "quiet", False),
    ("quiet", "hello", False),
    ("quiet", None, False),
    ("quiet", "Quiet", False),
])
def test_expected_sign_match_is_exact_and_fails_closed(predicted, expected, matched):
    assert expected_sign_matches(predicted, expected) is matched


def test_iot_backend_rejects_prediction_that_does_not_match_expected_sign():
    services = SimpleNamespace(
        settings=SimpleNamespace(iot_lock_active=False, iot_min_confidence=0.30),
        iot_enabled=True, iot_label_commands=MAPPINGS, last_iot_command_epoch=0.0, store=None, mqtt=None,
    )
    result = {
        "capture_id": str(uuid.uuid4()), "predicted_label": "quiet", "expected_label": "hello",
        "confidence": 0.99, "outcome": "accepted", "status": "accepted",
    }
    response = asyncio.run(maybe_issue_iot_command(services, result))
    assert response["status"] == "expected_sign_mismatch"
    assert services.mqtt is None


def test_iot_backend_requires_expected_sign_before_actuation():
    services = SimpleNamespace(
        settings=SimpleNamespace(iot_lock_active=False),
        iot_enabled=True, iot_label_commands=MAPPINGS, last_iot_command_epoch=0.0, store=None, mqtt=None,
    )
    result = {
        "capture_id": str(uuid.uuid4()), "predicted_label": "quiet", "expected_label": None,
        "confidence": 0.99, "outcome": "accepted", "status": "accepted",
    }
    response = asyncio.run(maybe_issue_iot_command(services, result))
    assert response["status"] == "expected_sign_required"
    assert services.mqtt is None


def test_iot_default_threshold_is_thirty_percent():
    now = 1_800_000_000.0
    command = build_command(
        capture_id=str(uuid.uuid4()), source_label="quiet", confidence=0.30,
        label_commands=MAPPINGS, now=now,
    )
    assert validate_command(command, label_commands=MAPPINGS, now=now + 1) == (True, "validated")

def test_command_timestamps_preserve_second_precision_on_esp8266():
    now = 1_794_862_583.5562735
    command = build_command(
        capture_id=str(uuid.uuid4()), source_label="quiet", confidence=0.90,
        label_commands=MAPPINGS, now=now, ttl_seconds=10,
    )

    assert isinstance(command["issued_at"], int)
    assert isinstance(command["expires_at"], int)
    assert command["issued_at"] == 1_794_862_583
    assert command["expires_at"] == 1_794_862_593
    assert command["expires_at"] - command["issued_at"] == 10
    assert validate_command(
        command, label_commands=MAPPINGS, now=command["issued_at"] + 1
    ) == (True, "validated")


def test_command_ttl_must_be_whole_seconds_for_esp8266():
    with pytest.raises(ValueError, match="whole seconds"):
        build_command(
            capture_id=str(uuid.uuid4()), source_label="hello", confidence=0.90,
            label_commands=MAPPINGS, now=1_800_000_000.25, ttl_seconds=10.5,
        )


def test_load_selected_esp8266_mappings(tmp_path):
    path = tmp_path / "iot_label_map.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "mappings": MAPPINGS,
    }), encoding="utf-8")

    assert load_label_commands(path, LABELS) == MAPPINGS


def test_hello_remains_unmapped():
    with pytest.raises(ValueError, match="no configured IoT mapping"):
        build_command(
            capture_id=str(uuid.uuid4()),
            source_label="hello",
            confidence=0.99,
            label_commands=MAPPINGS,
        )


@pytest.mark.parametrize("label,expected", [
    ("quiet", "QUIET_ACTION"),
    ("time", "TIME_ACTION"),
])
def test_build_and_validate_esp8266_commands(label, expected):
    now = 1_800_000_000.0
    command = make_command(label=label, now=now)

    assert command["command"] == expected
    assert command["source_label"] == label
    assert validate_command(
        command, label_commands=MAPPINGS, now=now + 1, min_confidence=0.30
    ) == (True, "validated")


def test_low_confidence_command_cannot_be_built():
    with pytest.raises(ValueError, match="Confidence"):
        make_command(label="hello", confidence=0.29)


def test_simulator_acknowledges_quiet_and_time_once():
    for label, command_name in (
        ("quiet", "QUIET_ACTION"),
        ("time", "TIME_ACTION"),
    ):
        command = make_command(label=label)
        simulator = SimulatedESP32(MAPPINGS, min_confidence=0.30)

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
        min_confidence=0.30,
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



def test_base_pre_iot_policy_stays_hard_locked_by_default():
    settings = replace(Settings(), iot_hardware_demo_enabled=False)
    assert settings.pre_iot_lockdown is True
    assert settings.iot_lock_active is True


def test_explicit_local_hardware_demo_only_changes_effective_lock():
    settings = replace(Settings(), iot_hardware_demo_enabled=True)
    assert settings.pre_iot_lockdown is True
    assert settings.iot_lock_active is False


def test_repository_esp8266_profile_contains_only_quiet_and_time():
    root = Path(__file__).resolve().parents[1]
    label_map = json.loads((root / "config" / "label_map_include50.json").read_text(encoding="utf-8"))
    demo_map_path = root / "config" / "iot_label_map.esp8266.json"

    assert label_map["hello"] == 22
    assert label_map["time"] == 43
    assert label_map["quiet"] == 34
    loaded_mappings = load_label_commands(demo_map_path, label_map)
    assert loaded_mappings == MAPPINGS
    assert "hello" not in loaded_mappings
    assert loaded_mappings["quiet"] == "QUIET_ACTION"

    default_map = json.loads((root / "config" / "iot_label_map.json").read_text(encoding="utf-8"))
    assert default_map["mappings"] == {}
