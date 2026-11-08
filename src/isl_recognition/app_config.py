from __future__ import annotations

import os
import math
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass


def _path_env(name: str, default: Path | None = None) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("ISL_HOST", "127.0.0.1")
    port: int = int(os.getenv("ISL_PORT", "8000"))
    database_path: Path = _path_env("ISL_DB_PATH", PROJECT_ROOT / "var" / "isl_events.sqlite3")  # type: ignore[assignment]
    ai4bharat_repo: Path | None = _path_env("AI4BHARAT_REPO")
    checkpoint: Path | None = _path_env("ISL_CHECKPOINT")
    label_map: Path | None = _path_env("ISL_LABEL_MAP")
    reference_video_dir: Path = _path_env("ISL_REFERENCE_VIDEO_DIR", PROJECT_ROOT / "data" / "include_videos")  # type: ignore[assignment]
    reference_metadata_path: Path = _path_env("ISL_REFERENCE_METADATA", PROJECT_ROOT / "data" / "include50_video_metadata.json")  # type: ignore[assignment]

    # Confidence policy:
    # 0.40 is a PROVISIONAL, UNCALIBRATED threshold chosen from exploratory benchmark sweep
    # on 29 reference clips (not independent held-out data). At 0.40, the sweep showed
    # 14/14 accepted clips correct (100% selective accuracy). This DOES NOT imply 100%
    # real-world accuracy. Tune on a proper validation set before any production use.
    # The UI always shows the raw score and an UNCALIBRATED warning.
    confidence_threshold: float = float(os.getenv("ISL_CONFIDENCE_THRESHOLD", "0.40"))

    min_capture_frames: int = int(os.getenv("ISL_MIN_CAPTURE_FRAMES", "12"))
    max_capture_seconds: float = float(os.getenv("ISL_MAX_CAPTURE_SECONDS", "6.0"))
    min_pose_ratio: float = float(os.getenv("ISL_MIN_POSE_RATIO", "0.50"))
    min_hand_ratio: float = float(os.getenv("ISL_MIN_HAND_RATIO", "0.30"))  # Lowered from 0.50; many valid signs use one hand and appear briefly
    max_extraction_failure_ratio: float = float(os.getenv("ISL_MAX_EXTRACTION_FAILURE_RATIO", "0.30"))
    max_frame_bytes: int = int(os.getenv("ISL_MAX_FRAME_BYTES", str(1_500_000)))
    # Retained only to avoid breaking existing .env files; the primary manual capture
    # path buffers JPEG frames directly and does not create a server-side frame queue.
    queue_size: int = int(os.getenv("ISL_FRAME_QUEUE_SIZE", "8"))

    # The current MediaPipe full-body detector is sliced to the INCLUDE upper-body
    # indices. This preserves the feature slots, but exact historical detector parity
    # is not established because the upstream upper_body_only model was removed.
    pose_compat_mode: str = "modern_full_pose_first25_index_compatible_not_exact_legacy"

    # A pre-IoT release must not be able to actuate devices.
    pre_iot_lockdown: bool = True
    iot_default_enabled: bool = os.getenv("ISL_IOT_ENABLED", "false").lower() in {"true", "1", "yes"}
    iot_cooldown_seconds: float = float(os.getenv("ISL_IOT_COOLDOWN_SECONDS", "5"))
    mqtt_host: str = os.getenv("MQTT_HOST", "127.0.0.1")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    iot_label_map_path: Path = _path_env("ISL_IOT_LABEL_MAP", PROJECT_ROOT / "config" / "iot_label_map.json")  # type: ignore[assignment]
    iot_min_confidence: float = float(os.getenv("ISL_IOT_MIN_CONFIDENCE", "0.70"))
    mqtt_username: str | None = os.getenv("MQTT_USERNAME") or None
    mqtt_password: str | None = os.getenv("MQTT_PASSWORD") or None
    mqtt_command_topic: str = os.getenv("MQTT_COMMAND_TOPIC", "isl/command/v1")
    mqtt_ack_topic: str = os.getenv("MQTT_ACK_TOPIC", "isl/ack/v1")
    mqtt_ack_timeout_seconds: float = float(os.getenv("MQTT_ACK_TIMEOUT_SECONDS", "5"))

    def validate(self) -> None:
        if not math.isfinite(self.confidence_threshold) or not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("ISL_CONFIDENCE_THRESHOLD must be finite and in [0,1]")
        if not math.isfinite(self.iot_min_confidence) or not 0.0 <= self.iot_min_confidence <= 1.0:
            raise ValueError("ISL_IOT_MIN_CONFIDENCE must be finite and in [0,1]")
        if self.min_capture_frames < 1 or self.min_capture_frames > 169:
            raise ValueError("ISL_MIN_CAPTURE_FRAMES must be between 1 and 169")
        if not math.isfinite(self.max_capture_seconds) or not 1.0 <= self.max_capture_seconds <= 6.0:
            raise ValueError("ISL_MAX_CAPTURE_SECONDS must be finite and between 1 and 6 seconds")
        if not math.isfinite(self.min_pose_ratio) or not 0.0 <= self.min_pose_ratio <= 1.0:
            raise ValueError("ISL_MIN_POSE_RATIO must be finite and in [0,1]")
        if not math.isfinite(self.min_hand_ratio) or not 0.0 <= self.min_hand_ratio <= 1.0:
            raise ValueError("ISL_MIN_HAND_RATIO must be finite and in [0,1]")
        if not math.isfinite(self.max_extraction_failure_ratio) or not 0.0 <= self.max_extraction_failure_ratio <= 1.0:
            raise ValueError("ISL_MAX_EXTRACTION_FAILURE_RATIO must be finite and in [0,1]")
        if self.queue_size < 1:
            raise ValueError("ISL_FRAME_QUEUE_SIZE must be >= 1")
        if self.max_frame_bytes < 1024:
            raise ValueError("ISL_MAX_FRAME_BYTES must be >= 1024")
        if not math.isfinite(self.iot_cooldown_seconds) or self.iot_cooldown_seconds < 0.0:
            raise ValueError("ISL_IOT_COOLDOWN_SECONDS must be finite and >= 0")
        if not math.isfinite(self.mqtt_ack_timeout_seconds) or self.mqtt_ack_timeout_seconds <= 0.0:
            raise ValueError("MQTT_ACK_TIMEOUT_SECONDS must be finite and > 0")
        if not 1 <= self.mqtt_port <= 65535:
            raise ValueError("MQTT_PORT must be between 1 and 65535")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The MVP backend must bind to loopback only")


def resolve_label_map(settings: Settings) -> Path | None:
    """Resolve explicit map, then a map in the official checkout, then bundled verified map."""
    if settings.label_map:
        return settings.label_map
    repo = settings.ai4bharat_repo
    if repo:
        known_official = repo / "label_maps" / "label_map_include50.json"
        if known_official.is_file():
            return known_official
        for folder in (repo / "label_maps", repo):
            if folder.is_dir():
                candidates = sorted(
                    p for p in folder.glob("*.json")
                    if "label" in p.name.lower() and "include" in p.name.lower()
                )
                if len(candidates) == 1:
                    return candidates[0]
    bundled = PROJECT_ROOT / "config" / "label_map_include50.json"
    return bundled if bundled.is_file() else None
