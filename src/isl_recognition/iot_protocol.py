from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid
from typing import Any

SCHEMA_VERSION = "1.0"
SUPPORTED_COMMANDS = {
    "LED_BLINK": 1,
    "LED_BLINK_TWICE": 2,
    "LED_BLINK_THREE": 3,
}
ACK_STATUSES = {"acknowledged", "rejected", "duplicate"}


def load_label_commands(path: Path, valid_labels: Mapping[str, int] | set[str]) -> dict[str, str]:
    """Load the user-selected two-label IoT allowlist.

    The shipped configuration is intentionally empty. Once the two demo signs are
    selected, the file must contain exactly two official INCLUDE-50 labels. It is
    never used to alter the classifier vocabulary or class-index ordering.
    """
    if not path.is_file():
        raise FileNotFoundError(f"IoT mapping file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("IoT mapping must be a JSON object with schema_version '1.0'.")
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        raise ValueError("IoT mapping 'mappings' must be a JSON object.")
    if len(mappings) not in {0, 2}:
        raise ValueError("Configure either zero mappings (disabled) or exactly two IoT sign mappings.")

    known_labels = set(valid_labels.keys()) if isinstance(valid_labels, Mapping) else set(valid_labels)
    cleaned: dict[str, str] = {}
    for raw_label, raw_command in mappings.items():
        if not isinstance(raw_label, str) or raw_label not in known_labels:
            raise ValueError(f"IoT mapping label is not an exact official INCLUDE-50 label: {raw_label!r}")
        if not isinstance(raw_command, str) or raw_command not in SUPPORTED_COMMANDS:
            raise ValueError(
                f"Unsupported IoT command for {raw_label!r}: {raw_command!r}. "
                f"Allowed commands: {', '.join(sorted(SUPPORTED_COMMANDS))}."
            )
        cleaned[raw_label] = raw_command
    return cleaned


def utc_iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(
        epoch if epoch is not None else time.time(), timezone.utc
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_command(
    *,
    capture_id: str,
    source_label: str,
    confidence: float,
    label_commands: Mapping[str, str] | None = None,
    now: float | None = None,
    ttl_seconds: float = 10.0,
    min_confidence: float = 0.70,
) -> dict[str, Any]:
    mappings = dict(label_commands or {})
    command_name = mappings.get(source_label)
    if command_name is None or command_name not in SUPPORTED_COMMANDS:
        raise ValueError("Label has no configured IoT mapping")
    score = float(confidence)
    threshold = float(min_confidence)
    ttl = float(ttl_seconds)
    if not math.isfinite(score) or not math.isfinite(threshold) or not threshold <= score <= 1.0:
        raise ValueError("Confidence is below the IoT command threshold or invalid")
    if not math.isfinite(ttl) or not 0.0 < ttl <= 30.0:
        raise ValueError("Command TTL must be finite and within (0, 30] seconds")
    if not isinstance(capture_id, str) or not capture_id or len(capture_id) > 128:
        raise ValueError("Capture ID must be a non-empty string of at most 128 characters")
    timestamp = time.time() if now is None else float(now)
    if not math.isfinite(timestamp):
        raise ValueError("Command timestamp must be finite")
    return {
        "schema_version": SCHEMA_VERSION,
        "command_id": str(uuid.uuid4()),
        "command": command_name,
        "source_label": source_label,
        "confidence": score,
        "capture_id": capture_id,
        "issued_at": timestamp,
        "expires_at": timestamp + ttl,
    }


def validate_command(
    payload: Any,
    *,
    label_commands: Mapping[str, str] | None = None,
    now: float | None = None,
    min_confidence: float = 0.70,
) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload_not_object"
    required = {"schema_version", "command_id", "command", "source_label", "confidence", "capture_id", "issued_at", "expires_at"}
    if not required.issubset(payload):
        return False, "missing_required_fields"
    if payload.get("schema_version") != SCHEMA_VERSION:
        return False, "unsupported_schema_version"
    try:
        uuid.UUID(str(payload["command_id"]))
    except (ValueError, TypeError, AttributeError):
        return False, "invalid_command_id"
    source_label = payload.get("source_label")
    mappings = dict(label_commands or {})
    expected_command = mappings.get(source_label) if isinstance(source_label, str) else None
    if expected_command is None or expected_command not in SUPPORTED_COMMANDS or payload.get("command") != expected_command:
        return False, "command_not_allowlisted"
    try:
        confidence = float(payload["confidence"])
        issued = float(payload["issued_at"])
        expires = float(payload["expires_at"])
        minimum = float(min_confidence)
    except (ValueError, TypeError):
        return False, "invalid_numeric_field"
    if not all(math.isfinite(v) for v in (confidence, issued, expires, minimum)):
        return False, "non_finite_numeric_field"
    if not 0.0 <= minimum <= 1.0 or not minimum <= confidence <= 1.0:
        return False, "confidence_out_of_range"
    current = time.time() if now is None else float(now)
    if not math.isfinite(current):
        return False, "invalid_current_time"
    if issued > current + 5:
        return False, "issued_in_future"
    if expires <= current:
        return False, "command_expired"
    if expires <= issued or expires - issued > 30:
        return False, "invalid_expiry_window"
    if not isinstance(payload.get("capture_id"), str) or not payload["capture_id"] or len(payload["capture_id"]) > 128:
        return False, "invalid_capture_id"
    return True, "validated"


class SimulatedESP32:
    """Deterministic endpoint simulator; no physical GPIO or real LED is touched."""

    def __init__(
        self,
        label_commands: Mapping[str, str] | None = None,
        min_confidence: float = 0.70,
    ):
        self.label_commands = dict(label_commands or {})
        self.min_confidence = float(min_confidence)
        self.seen: set[str] = set()
        self.seen_captures: set[str] = set()
        self.led_blinks = 0

    def process(self, payload: Any, *, now: float | None = None) -> dict[str, Any]:
        command_id = payload.get("command_id") if isinstance(payload, dict) else None
        capture_id = payload.get("capture_id") if isinstance(payload, dict) else None
        if command_id and command_id in self.seen:
            status, detail = "duplicate", "command_id_already_processed"
        elif capture_id and capture_id in self.seen_captures:
            status, detail = "duplicate", "capture_id_already_processed"
        else:
            ok, detail = validate_command(
                payload,
                label_commands=self.label_commands,
                now=now,
                min_confidence=self.min_confidence,
            )
            if not ok:
                status = "rejected"
            else:
                self.seen.add(command_id)
                self.seen_captures.add(str(payload["capture_id"]))
                pulse_count = SUPPORTED_COMMANDS[str(payload["command"])]
                self.led_blinks += pulse_count
                details = {
                    1: "simulated_led_blink_processed",
                    2: "simulated_led_double_blink_processed",
                    3: "simulated_led_triple_blink_processed",
                }
                status, detail = "acknowledged", details[pulse_count]
        return {
            "schema_version": SCHEMA_VERSION,
            "command_id": command_id or "",
            "status": status,
            "detail": detail,
            "timestamp_utc": utc_iso(now),
        }
