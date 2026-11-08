from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import time


def numeric_summary(values):
    vals = [float(x) for x in values if x is not None]
    if not vals:
        return {"samples": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None}
    import numpy as np
    return {"samples": len(vals), "mean": statistics.mean(vals), "median": statistics.median(vals), "p95": float(np.percentile(vals, 95)), "min": min(vals), "max": max(vals)}


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Summarise actual capture metrics and MQTT acknowledgement timing stored by the app.")
    parser.add_argument("--db-path", default="var/isl_events.sqlite3")
    parser.add_argument("--out", default="outputs/system_benchmark.json")
    args = parser.parse_args()
    db = Path(args.db_path)
    if not db.is_file():
        print(f"No SQLite event database found at {db}; run the app and perform real captures first.", file=sys.stderr)
        return 2
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        recognition = [dict(r) for r in conn.execute("SELECT * FROM recognition_events ORDER BY id")]
        iot = [dict(r) for r in conn.execute("SELECT * FROM iot_events ORDER BY id")]
    ack_times = []
    for row in iot:
        published, ack = parse_iso(row.get("publish_attempt_at")), parse_iso(row.get("ack_at"))
        if published is not None and ack is not None and row.get("ack_status") in {"acknowledged", "rejected", "duplicate"}:
            ack_times.append(max(0.0, (ack-published)*1000.0))
    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "database": str(db.resolve()),
        "recognition_events_total": len(recognition),
        "recognition_outcomes": {outcome: sum(1 for r in recognition if r.get("outcome") == outcome) for outcome in sorted({r.get("outcome") for r in recognition})},
        "capture_metrics": {
            "capture_duration_ms": numeric_summary([r.get("capture_duration_ms") for r in recognition]),
            "mediapipe_per_frame_ms_median_of_capture_medians": numeric_summary([r.get("mediapipe_latency_ms") for r in recognition]),
            "model_inference_ms": numeric_summary([r.get("inference_latency_ms") for r in recognition]),
            "post_capture_processing_ms": numeric_summary([r.get("processing_latency_ms") for r in recognition]),
            "capture_start_to_result_ms": numeric_summary([r.get("end_to_end_latency_ms") for r in recognition]),
            "effective_processed_fps": numeric_summary([r.get("effective_fps") for r in recognition]),
            "feature_frames_per_capture": numeric_summary([r.get("frame_count") for r in recognition]),
            "dropped_frames_per_capture": numeric_summary([r.get("frames_dropped") for r in recognition]),
            "received_frames_per_capture": numeric_summary([r.get("received_frames") for r in recognition]),
        },
        "iot_events_total": len(iot),
        "iot_ack_status_counts": {status: sum(1 for r in iot if r.get("ack_status") == status) for status in sorted({r.get("ack_status") for r in iot})},
        "mqtt_publish_to_ack_ms": numeric_summary(ack_times),
        "runtime_resources": {"cpu_logical_cores": __import__("os").cpu_count(), "ram_total_gib": None, "gpu": "not probed"},
        "evidence_notes": [
            "Values are derived only from stored local app events; a missing sample is not imputed.",
            "MediaPipe values are per-frame latencies summarised per capture; the aggregate is the distribution of per-capture medians.",
            "MQTT publish-to-ack starts at the timestamp recorded immediately before the Paho publish call and ends at subscriber ACK receipt; it is not proof of physical LED output.",
            "Live camera, CPU/GPU and RAM metrics require actual local runs; no system benchmark result is fabricated by this script."
        ]
    }
    try:
        import psutil
        report["runtime_resources"]["ram_total_gib"] = round(psutil.virtual_memory().total / (1024**3), 3)
        report["runtime_resources"]["ram_available_gib_at_report"] = round(psutil.virtual_memory().available / (1024**3), 3)
        report["runtime_resources"]["cpu_percent_at_report"] = psutil.cpu_percent(interval=0.5)
    except ImportError:
        report["runtime_resources"]["resource_note"] = "Install psutil for RAM/CPU snapshot; not required for core app."
    try:
        import torch
        report["runtime_resources"]["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CUDA not available to current PyTorch"
    except Exception as exc:
        report["runtime_resources"]["gpu"] = f"PyTorch device probe unavailable: {type(exc).__name__}"
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
