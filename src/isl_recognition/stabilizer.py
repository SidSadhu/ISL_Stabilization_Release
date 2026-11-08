from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class StablePrediction:
    label: str | None
    confidence: float
    is_stable: bool


class PredictionStabilizer:
    """Configurable post-processing; raw model output remains untouched."""

    def __init__(
        self,
        history_size: int = 5,
        confidence_threshold: float = 0.70,
        probability_alpha: float = 0.50,
        mode: str = "threshold_smoothing",
    ):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in [0,1]")
        if not 0.0 < probability_alpha <= 1.0:
            raise ValueError("probability_alpha must be in (0,1]")
        if history_size < 1:
            raise ValueError("history_size must be >= 1")
        if mode not in {"threshold", "majority", "smoothing", "threshold_smoothing"}:
            raise ValueError(f"Unknown stabilization mode: {mode}")
        self.history_size = history_size
        self.confidence_threshold = confidence_threshold
        self.alpha = probability_alpha
        self.mode = mode
        self.history: deque[str] = deque(maxlen=history_size)
        self.smoothed: np.ndarray | None = None

    def reset(self) -> None:
        self.history.clear()
        self.smoothed = None

    def update(self, probabilities: np.ndarray, labels: list[str]) -> StablePrediction:
        probs = np.asarray(probabilities, dtype=np.float32)
        if probs.ndim != 1 or len(probs) != len(labels):
            raise ValueError("probabilities and labels must be 1-D and equal length")
        if not np.isfinite(probs).all():
            raise ValueError("probabilities contain NaN/Inf")
        total = float(probs.sum())
        if total <= 0:
            raise ValueError("probabilities must have positive sum")
        probs = probs / total

        if self.mode in {"smoothing", "threshold_smoothing"}:
            if self.smoothed is None:
                self.smoothed = probs
            else:
                self.smoothed = self.alpha * probs + (1.0 - self.alpha) * self.smoothed
            used = self.smoothed
        else:
            used = probs

        idx = int(np.argmax(used))
        label = labels[idx]
        confidence = float(used[idx])
        self.history.append(label)

        if self.mode in {"majority", "threshold_smoothing"} and self.history:
            counts = {x: self.history.count(x) for x in set(self.history)}
            label = max(counts, key=counts.get)
            label_conf = confidence
            stable = counts[label] >= max(2, (len(self.history) + 1) // 2) and confidence >= self.confidence_threshold
            return StablePrediction(label if stable else None, label_conf, stable)

        stable = confidence >= self.confidence_threshold
        return StablePrediction(label if stable else None, confidence, stable)
