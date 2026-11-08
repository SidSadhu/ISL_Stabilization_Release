from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PreprocessConfig:
    max_frame_len: int = 169
    feature_dim: int = 134
    frame_width: float = 1920.0
    frame_height: float = 1080.0
    pose_landmarks: int = 25
    hand_landmarks: int = 21


class PreprocessError(ValueError):
    pass


def _component_xy(x_values: Sequence[float], y_values: Sequence[float]) -> np.ndarray:
    x = np.asarray(x_values, dtype=np.float32)
    y = np.asarray(y_values, dtype=np.float32)
    if x.ndim != 2 or y.ndim != 2 or x.shape != y.shape:
        raise PreprocessError(f"x/y shapes must match as 2-D arrays, got {x.shape} and {y.shape}")
    return np.stack([x, y], axis=-1)


def interpolate_and_scale(
    arr_xy: np.ndarray,
    width: float = 1920.0,
    height: float = 1080.0,
) -> np.ndarray:
    """Reproduce the verified AI4Bharat preprocessing semantics.

    Missing coordinates are linearly interpolated across time with both-direction
    filling. Entirely missing coordinate arrays become zero. X/Y coordinates are
    then scaled by 1920/1080 respectively.
    """
    arr_xy = np.asarray(arr_xy, dtype=np.float32)
    if arr_xy.ndim != 3 or arr_xy.shape[-1] != 2:
        raise PreprocessError(f"Expected (frames, landmarks, 2), got {arr_xy.shape}")

    x = pd.DataFrame(arr_xy[:, :, 0]).interpolate(
        method="linear", limit_direction="both"
    ).to_numpy(dtype=np.float32)
    y = pd.DataFrame(arr_xy[:, :, 1]).interpolate(
        method="linear", limit_direction="both"
    ).to_numpy(dtype=np.float32)

    if np.count_nonzero(~np.isnan(x)) == 0:
        x = np.zeros(x.shape, dtype=np.float32)
    if np.count_nonzero(~np.isnan(y)) == 0:
        y = np.zeros(y.shape, dtype=np.float32)

    x = np.where(np.isnan(x), 0.0, x).astype(np.float32)
    y = np.where(np.isnan(y), 0.0, y).astype(np.float32)
    x *= width
    y *= height
    return np.stack([x, y], axis=-1).astype(np.float32)


def preprocess_record(
    row: Mapping[str, object],
    config: PreprocessConfig | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert an INCLUDE-style keypoint JSON record to raw and padded tensors."""
    config = config or PreprocessConfig()

    pose_x = row["pose_x"]
    pose_y = row["pose_y"]
    hand1_x = row["hand1_x"]
    hand1_y = row["hand1_y"]
    hand2_x = row["hand2_x"]
    hand2_y = row["hand2_y"]

    pose = interpolate_and_scale(
        _component_xy(pose_x, pose_y), config.frame_width, config.frame_height
    )
    hand1 = interpolate_and_scale(
        _component_xy(hand1_x, hand1_y), config.frame_width, config.frame_height
    )
    hand2 = interpolate_and_scale(
        _component_xy(hand2_x, hand2_y), config.frame_width, config.frame_height
    )

    pose = pose.reshape(-1, 50)
    hand1 = hand1.reshape(-1, 42)
    hand2 = hand2.reshape(-1, 42)
    data = np.concatenate([pose, hand1, hand2], axis=-1).astype(np.float32)

    if data.shape[1] != config.feature_dim:
        raise PreprocessError(f"Expected {config.feature_dim} features/frame, got {data.shape[1]}")
    if not np.isfinite(data).all():
        raise PreprocessError("Preprocessed data contains NaN/Inf")

    if data.shape[0] > config.max_frame_len:
        data = data[: config.max_frame_len]

    padded = np.zeros(
        (config.max_frame_len, config.feature_dim), dtype=np.float32
    )
    padded[: data.shape[0]] = data
    return data, padded


def make_scaled_frame_feature(
    pose_x: Sequence[float], pose_y: Sequence[float],
    hand1_x: Sequence[float], hand1_y: Sequence[float],
    hand2_x: Sequence[float], hand2_y: Sequence[float],
    config: PreprocessConfig | None = None,
    allow_nan: bool = False,
) -> np.ndarray:
    """Build one 134-D frame and apply x*1920/y*1080 scaling."""
    config = config or PreprocessConfig()
    parts = []
    for x_values, y_values, n in [
        (pose_x, pose_y, 25),
        (hand1_x, hand1_y, 21),
        (hand2_x, hand2_y, 21),
    ]:
        if len(x_values) != n or len(y_values) != n:
            raise PreprocessError(f"Expected {n} X/Y values")
        arr = np.stack([
            np.asarray(x_values, dtype=np.float32) * config.frame_width,
            np.asarray(y_values, dtype=np.float32) * config.frame_height,
        ], axis=-1)
        parts.append(arr.reshape(-1))
    frame = np.concatenate(parts).astype(np.float32)
    if frame.shape != (config.feature_dim,):
        raise PreprocessError(f"Expected frame shape ({config.feature_dim},), got {frame.shape}")
    if allow_nan:
        if np.isinf(frame).any():
            raise PreprocessError("Frame contains Inf")
    elif not np.isfinite(frame).all():
        raise PreprocessError("Frame contains NaN/Inf")
    return frame


def interpolate_scaled_sequence(frames: np.ndarray) -> np.ndarray:
    """Interpolate missing values in an already scaled 2-D feature sequence."""
    frames = np.asarray(frames, dtype=np.float32)
    if frames.ndim != 2 or frames.shape[1] != 134:
        raise PreprocessError(f"Expected (frames,134), got {frames.shape}")
    if np.isinf(frames).any():
        raise PreprocessError("Live sequence contains Inf")
    out = np.empty_like(frames)
    for col in range(frames.shape[1]):
        series = pd.Series(frames[:, col], dtype="float32")
        if series.notna().any():
            out[:, col] = series.interpolate(method="linear", limit_direction="both").fillna(0.0).to_numpy(dtype=np.float32)
        else:
            out[:, col] = 0.0
    if not np.isfinite(out).all():
        raise PreprocessError("Interpolated sequence contains NaN/Inf")
    return out


def preprocess_feature_sequence(
    frames: np.ndarray,
    max_frame_len: int = 169,
    feature_dim: int = 134,
) -> tuple[np.ndarray, np.ndarray]:
    """Pad/truncate an already scaled feature sequence for model input."""
    frames = np.asarray(frames, dtype=np.float32)
    if frames.ndim != 2 or frames.shape[1] != feature_dim:
        raise PreprocessError(f"Expected (frames,{feature_dim}), got {frames.shape}")
    if np.isinf(frames).any():
        raise PreprocessError("Sequence contains Inf")
    if np.isnan(frames).any():
        frames = interpolate_scaled_sequence(frames)

    raw = frames[:max_frame_len]
    padded = np.zeros((max_frame_len, feature_dim), dtype=np.float32)
    padded[:len(raw)] = raw
    return raw, padded
