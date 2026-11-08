from __future__ import annotations

from collections import deque
import numpy as np


class TemporalBuffer:
    """Fixed-length rolling buffer with zero-padding for short windows."""

    def __init__(self, maxlen: int = 169, feature_dim: int = 134, allow_nan: bool = False):
        self.maxlen = int(maxlen)
        self.feature_dim = int(feature_dim)
        self.allow_nan = allow_nan
        self._frames: deque[np.ndarray] = deque(maxlen=self.maxlen)

    def append(self, frame: np.ndarray) -> None:
        frame = np.asarray(frame, dtype=np.float32)
        if frame.shape != (self.feature_dim,):
            raise ValueError(f"Expected frame shape ({self.feature_dim},), got {frame.shape}")
        if self.allow_nan:
            if np.isinf(frame).any():
                raise ValueError("TemporalBuffer received Inf")
        elif not np.isfinite(frame).all():
            raise ValueError("TemporalBuffer received NaN/Inf")
        self._frames.append(frame.copy())

    def __len__(self) -> int:
        return len(self._frames)

    def clear(self) -> None:
        self._frames.clear()

    def padded(self) -> np.ndarray:
        out = np.zeros((self.maxlen, self.feature_dim), dtype=np.float32)
        if self._frames:
            arr = np.stack(list(self._frames), axis=0)
            out[:len(arr)] = arr
        return out

    def snapshot(self) -> np.ndarray:
        if not self._frames:
            return np.empty((0, self.feature_dim), dtype=np.float32)
        return np.stack(list(self._frames), axis=0)
