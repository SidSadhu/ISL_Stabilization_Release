from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.labels import load_label_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure real checkpoint latency on saved feature tensors; does not fabricate webcam/IoT timing.")
    parser.add_argument("--features-npy", required=True, help="NumPy array shaped (samples,169,134), prepared from real captures")
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--samples", type=int, default=0, help="0 means all tensor samples")
    parser.add_argument("--out", default="outputs/runtime_benchmark.json")
    args = parser.parse_args()
    settings = Settings()
    label_map_path = resolve_label_map(settings)
    if not settings.ai4bharat_repo or not settings.ai4bharat_repo.is_dir() or not settings.checkpoint or not settings.checkpoint.is_file() or not label_map_path or not label_map_path.is_file():
        print("BLOCKED: Configure AI4BHARAT_REPO, ISL_CHECKPOINT and official label map before benchmarking.", file=sys.stderr); return 2
    load_label_map(label_map_path)
    tensors = np.load(args.features_npy, allow_pickle=False)
    if tensors.ndim != 3 or tuple(tensors.shape[1:]) != (169, 134) or not np.isfinite(tensors).all():
        print("Input must be finite float array shaped (N,169,134).", file=sys.stderr); return 2
    if args.samples > 0: tensors = tensors[:args.samples]
    if not len(tensors):
        print("No samples provided.", file=sys.stderr); return 2
    try:
        from isl_recognition.model import INCLUDETransformerSmall
        model = INCLUDETransformerSmall(settings.ai4bharat_repo, settings.checkpoint)
    except Exception as exc:
        print(f"BLOCKED: Could not initialise official model/dependencies: {type(exc).__name__}: {exc}", file=sys.stderr); return 2
    warmup = min(max(args.warmup, 0), max(0, len(tensors) - 1))
    for x in tensors[:warmup]: model.predict(x.astype(np.float32))
    measured_tensors = tensors[warmup:]
    latencies = []
    for x in measured_tensors:
        t0 = time.perf_counter(); model.predict(x.astype(np.float32)); latencies.append((time.perf_counter()-t0)*1000.0)
    report = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0], "platform": platform.platform(),
        "device": str(model.device), "checkpoint": str(settings.checkpoint),
        "input_file": str(Path(args.features_npy).resolve()), "selected_input_shape": list(tensors.shape), "measured_input_shape": list(measured_tensors.shape),
        "warmup_samples": warmup, "measured_samples": len(latencies),
        "model_inference_ms_per_sample": {
            "mean": statistics.mean(latencies), "median": statistics.median(latencies),
            "p95": float(np.percentile(latencies,95)), "min": min(latencies), "max": max(latencies),
        },
        "warning": "These measurements are model-only on the supplied tensors. They do not measure webcam FPS, MediaPipe, capture-to-text, MQTT acknowledgement or physical actuation.",
    }
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
