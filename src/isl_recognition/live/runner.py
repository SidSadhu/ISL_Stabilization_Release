from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np
import torch

from ..buffer import TemporalBuffer
from ..config import AppConfig
from ..labels import load_label_map, invert_label_map
from ..model import INCLUDETransformerSmall
from ..preprocess import make_scaled_frame_feature, preprocess_feature_sequence
from ..stabilizer import PredictionStabilizer


def _stderr_pump(pipe):
    for line in iter(pipe.readline, ""):
        print(f"[MediaPipe] {line.rstrip()}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live isolated ISL recognition")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--mp-python", type=Path, default=Path(os.getenv("ISL_MP_PYTHON", "/content/isl_mediapipe_env/bin/python")))
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--label-map", type=Path, default=None)
    parser.add_argument("--min-frames", type=int, default=30)
    parser.add_argument("--predict-every", type=int, default=5)
    parser.add_argument("--stabilization", choices=["threshold", "majority", "smoothing", "threshold_smoothing"], default="threshold_smoothing")
    parser.add_argument("--hand-order-mode", choices=["verified_one_hand_fix", "wrist_nearest"], default="verified_one_hand_fix")
    args = parser.parse_args()

    cfg = AppConfig()
    repo = args.repo or cfg.repo
    checkpoint = args.checkpoint or cfg.checkpoint
    label_map_path = args.label_map or cfg.label_map

    labels_map = load_label_map(label_map_path)
    idx_to_label = invert_label_map(labels_map)
    labels = [idx_to_label[i] for i in range(50)]

    model = INCLUDETransformerSmall(repo, checkpoint)
    buffer = TemporalBuffer(169, 134, allow_nan=True)
    stabilizer = PredictionStabilizer(mode=args.stabilization)

    cmd = [
        str(args.mp_python), "-m", "isl_recognition.live.worker",
        "--camera", str(args.camera),
        "--hand-order-mode", args.hand_order_mode,
    ]
    env = os.environ.copy()
    root = str(repo.parent)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + root + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
    threading.Thread(target=_stderr_pump, args=(proc.stderr,), daemon=True).start()

    frame_counter = 0
    last_infer_frame = -1
    try:
        for line in iter(proc.stdout.readline, ""):
            msg = json.loads(line)
            if msg.get("type") != "frame":
                continue
            frame = make_scaled_frame_feature(
                msg["pose_x"], msg["pose_y"],
                msg["hand1_x"], msg["hand1_y"],
                msg["hand2_x"], msg["hand2_y"],
                cfg,
                allow_nan=True,
            )
            buffer.append(frame)
            frame_counter += 1

            if len(buffer) < args.min_frames or frame_counter % args.predict_every != 0:
                continue

            t0 = time.perf_counter()
            raw = buffer.snapshot()
            raw, padded = preprocess_feature_sequence(raw, 169, 134)
            infer = model.predict(padded)
            model_ms = (time.perf_counter() - t0) * 1000.0
            probs = np.asarray(infer["probabilities"], dtype=np.float32)
            stable = stabilizer.update(probs, labels)
            end_to_end_ms = max(0.0, time.time() - float(msg["timestamp"])) * 1000.0
            raw_label = idx_to_label[infer["predicted_index"]]
            print(
                f"frames={len(buffer):3d} raw={raw_label:<14} "
                f"raw_conf={infer['confidence']*100:6.2f}% "
                f"stable={str(stable.label):<14} "
                f"mp_ms={float(msg['mediapipe_ms']):7.2f} "
                f"model_ms={model_ms:7.2f} "
                f"e2e_ms={end_to_end_ms:7.2f}",
                flush=True,
            )
            if proc.stdin:
                proc.stdin.write(json.dumps({
                    "type": "prediction",
                    "label": stable.label or raw_label,
                    "confidence": stable.confidence,
                    "stable": stable.is_stable,
                }) + "\n")
                proc.stdin.flush()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
