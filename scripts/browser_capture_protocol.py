"""Binary protocol for browser-native INCLUDE-50 Canvas capture diagnostics.

The protocol is intentionally local/offline: each upload contains one validation
clip, its JSON metadata header, and the exact JPEG bytes emitted by Canvas
`toDataURL('image/jpeg', 0.92)`. It contains no MQTT or application command path.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

MAGIC = b"ISLBRW01"
PREFIX = struct.Struct(">I")
MAX_HEADER_BYTES = 2_000_000
MAX_FRAME_COUNT = 169
MAX_FRAME_BYTES = 1_500_000
EXPECTED_CONFIG = {
    "canvas_width": 640,
    "jpeg_quality": 0.92,
    "timer_interval_ms": 40,
    "max_capture_seconds": 6.0,
    "max_frames": 169,
    "diagnostic_mode": True,
    "iot_enabled": False,
    "iot_command_emission": False,
    "classifier_input_mirrored": False,
}


class CaptureBundleError(ValueError):
    """Raised when a browser capture upload violates the diagnostic contract."""


@dataclass(frozen=True)
class ParsedBrowserCapture:
    header: dict[str, Any]
    frames: tuple[bytes, ...]
    sha256: str


def normalize_video_path(value: str) -> str:
    raw = str(value).replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
        raise CaptureBundleError(f"Unsafe or empty validation video path: {value!r}")
    return str(path)


def _finite_number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise CaptureBundleError(f"{name} must be a finite number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise CaptureBundleError(f"{name} must be >= {minimum}")
    return number


def encode_capture(header: Mapping[str, Any], frames: list[bytes] | tuple[bytes, ...]) -> bytes:
    """Create a deterministic single-clip payload (also used by protocol tests)."""
    normalized = dict(header)
    frame_rows = normalized.get("case", {}).get("frames") if isinstance(normalized.get("case"), dict) else None
    if not isinstance(frame_rows, list) or len(frame_rows) != len(frames):
        raise CaptureBundleError("case.frames metadata must describe every image payload")
    for row, payload in zip(frame_rows, frames):
        row["jpeg_bytes"] = len(payload)
    header_bytes = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise CaptureBundleError("Capture header is too large")
    return MAGIC + PREFIX.pack(len(header_bytes)) + header_bytes + b"".join(frames)


def parse_capture_payload(
    payload: bytes,
    expected_row: Mapping[str, str] | None = None,
    *,
    require_production_config: bool = True,
) -> ParsedBrowserCapture:
    """Validate and decode a single browser clip payload without writing files."""
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise CaptureBundleError("Capture payload must be bytes")
    data = bytes(payload)
    if len(data) < len(MAGIC) + PREFIX.size or data[: len(MAGIC)] != MAGIC:
        raise CaptureBundleError("Invalid browser capture magic/version")
    header_len = PREFIX.unpack_from(data, len(MAGIC))[0]
    if header_len < 2 or header_len > MAX_HEADER_BYTES:
        raise CaptureBundleError("Invalid or oversized browser capture header")
    header_start = len(MAGIC) + PREFIX.size
    frames_start = header_start + header_len
    if frames_start > len(data):
        raise CaptureBundleError("Capture header extends beyond payload")
    try:
        header = json.loads(data[header_start:frames_start].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureBundleError(f"Invalid capture JSON header: {exc}") from exc
    if not isinstance(header, dict) or header.get("schema_version") != 1:
        raise CaptureBundleError("Unsupported browser capture schema")
    case = header.get("case")
    config = header.get("capture_config")
    if not isinstance(case, dict) or not isinstance(config, dict):
        raise CaptureBundleError("Capture header must contain case and capture_config objects")
    if require_production_config:
        for key, expected in EXPECTED_CONFIG.items():
            value = config.get(key)
            if isinstance(expected, float):
                if _finite_number(value, f"capture_config.{key}") != expected:
                    raise CaptureBundleError(f"capture_config.{key} must equal {expected}")
            elif value != expected:
                raise CaptureBundleError(f"capture_config.{key} must equal {expected!r}")
    video_path = normalize_video_path(case.get("video_path", ""))
    label = case.get("label")
    if not isinstance(label, str) or not label.strip():
        raise CaptureBundleError("case.label must be a non-empty class label")
    if expected_row is not None:
        expected_path = normalize_video_path(str(expected_row.get("video_path", "")))
        expected_label = str(expected_row.get("label", "")).strip()
        if video_path.casefold() != expected_path.casefold() or label.strip() != expected_label:
            raise CaptureBundleError(
                f"Capture identity mismatch: got {video_path!r}/{label!r}; "
                f"expected {expected_path!r}/{expected_label!r}"
            )
    frames_meta = case.get("frames")
    if not isinstance(frames_meta, list) or not 1 <= len(frames_meta) <= MAX_FRAME_COUNT:
        raise CaptureBundleError(f"case.frames must contain 1..{MAX_FRAME_COUNT} records")
    if case.get("frame_count") != len(frames_meta):
        raise CaptureBundleError("case.frame_count does not match case.frames length")
    if not isinstance(case.get("source_name"), str) or not case["source_name"].strip():
        raise CaptureBundleError("case.source_name must be a non-empty local filename")
    source_width = case.get("source_width")
    source_height = case.get("source_height")
    if isinstance(source_width, bool) or not isinstance(source_width, int) or source_width < 1:
        raise CaptureBundleError("case.source_width must be a positive integer")
    if isinstance(source_height, bool) or not isinstance(source_height, int) or source_height < 1:
        raise CaptureBundleError("case.source_height must be a positive integer")
    cursor = frames_start
    frames: list[bytes] = []
    previous_time = -1.0
    expected_canvas_height = round(640 * source_height / source_width)
    for index, meta in enumerate(frames_meta):
        if not isinstance(meta, dict):
            raise CaptureBundleError(f"Frame metadata {index} must be an object")
        timestamp = _finite_number(meta.get("video_time_seconds"), f"frames[{index}].video_time_seconds", minimum=0)
        _finite_number(meta.get("elapsed_ms"), f"frames[{index}].elapsed_ms", minimum=0)
        if timestamp <= previous_time:
            raise CaptureBundleError("Browser video timestamps must be strictly increasing")
        previous_time = timestamp
        if meta.get("width") != 640 or meta.get("height") != expected_canvas_height:
            raise CaptureBundleError(
                f"Frame {index} dimensions must be 640x{expected_canvas_height} to match the production canvas"
            )
        frame_size = meta.get("jpeg_bytes")
        if isinstance(frame_size, bool) or not isinstance(frame_size, int) or not 4 <= frame_size <= MAX_FRAME_BYTES:
            raise CaptureBundleError(f"Frame {index} JPEG size is invalid or exceeds {MAX_FRAME_BYTES} bytes")
        if cursor + frame_size > len(data):
            raise CaptureBundleError(f"Frame {index} payload is truncated")
        jpeg = data[cursor:cursor + frame_size]
        if not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
            raise CaptureBundleError(f"Frame {index} is not a complete JPEG image")
        frames.append(jpeg)
        cursor += frame_size
    if cursor != len(data):
        raise CaptureBundleError("Unexpected trailing bytes after the final JPEG frame")
    if len(frames) < 12:
        raise CaptureBundleError("Capture contains fewer than the application minimum of 12 frames")
    for key in ("timer_ticks", "duplicate_frame_skips", "browser_total_video_frames", "browser_dropped_video_frames"):
        value = case.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CaptureBundleError(f"case.{key} must be a non-negative integer")
    if case["timer_ticks"] < len(frames):
        raise CaptureBundleError("case.timer_ticks cannot be less than submitted frames")
    capture_duration = _finite_number(case.get("capture_duration_ms"), "case.capture_duration_ms", minimum=0)
    if capture_duration > 6100:
        raise CaptureBundleError("case.capture_duration_ms exceeds the 6-second production capture window")
    digest = hashlib.sha256(data).hexdigest()
    return ParsedBrowserCapture(header=header, frames=tuple(frames), sha256=digest)
