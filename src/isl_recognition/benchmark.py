from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from statistics import mean, median
import time


@dataclass
class TimingSample:
    mediapipe_ms: float
    model_ms: float
    end_to_end_ms: float


@dataclass
class BenchmarkSummary:
    samples: int
    mediapipe_mean_ms: float
    mediapipe_median_ms: float
    model_mean_ms: float
    model_median_ms: float
    end_to_end_mean_ms: float
    end_to_end_median_ms: float
    effective_fps: float


def summarize(samples: list[TimingSample]) -> BenchmarkSummary:
    if not samples:
        raise ValueError("No benchmark samples")
    mp = [s.mediapipe_ms for s in samples]
    model = [s.model_ms for s in samples]
    e2e = [s.end_to_end_ms for s in samples]
    return BenchmarkSummary(
        samples=len(samples),
        mediapipe_mean_ms=mean(mp),
        mediapipe_median_ms=median(mp),
        model_mean_ms=mean(model),
        model_median_ms=median(model),
        end_to_end_mean_ms=mean(e2e),
        end_to_end_median_ms=median(e2e),
        effective_fps=1000.0 / mean(e2e),
    )


def save_summary(summary: BenchmarkSummary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")
