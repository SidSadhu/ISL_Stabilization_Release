from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration; paths may be overridden with environment variables."""

    repo: Path = Path(os.getenv("AI4BHARAT_REPO", "/content/AI4Bharat_INCLUDE"))
    checkpoint: Path = Path(
        os.getenv(
            "ISL_CHECKPOINT",
            "/content/AI4Bharat_INCLUDE/checkpoints/include50_no_cnn_transformer_small.pth",
        )
    )
    label_map: Path = Path(
        os.getenv(
            "ISL_LABEL_MAP",
            "/content/AI4Bharat_INCLUDE/label_maps/label_map_include50.json",
        )
    )
    feature_dim: int = 134
    max_frame_len: int = 169
    num_classes: int = 50
    frame_width: float = 1920.0
    frame_height: float = 1080.0
    pose_landmarks: int = 25
    hand_landmarks: int = 21


DEFAULT_CONFIG = AppConfig()
