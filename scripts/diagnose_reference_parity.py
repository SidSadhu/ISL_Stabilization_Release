#!/usr/bin/env python
"""Compare the app extractor with the official AI4Bharat INCLUDE extractor.

This is a single-clip diagnostic, not a dataset accuracy evaluation. It generates
features both ways for the same local video, runs the configured INCLUDE-50
checkpoint on each input, and probes repeated-inference stability.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import inspect
from types import SimpleNamespace
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _interpolate_columns(matrix):
    import numpy as np
    values = np.asarray(matrix, dtype=np.float32).copy()
    if values.ndim != 2:
        raise ValueError(f"Expected 2-D time x landmark array, got {values.shape}")
    n_frames, n_points = values.shape
    x = np.arange(n_frames)
    for col in range(n_points):
        valid = np.isfinite(values[:, col])
        if valid.any():
            values[:, col] = np.interp(x, x[valid], values[valid, col])
        else:
            values[:, col] = 0.0
    return values


def official_json_to_padded(record_path: Path):
    """Mirror INCLUDE evaluate.py interpolation, coordinate scale, concat and pad."""
    import numpy as np
    row = json.loads(record_path.read_text(encoding="utf-8"))
    parts = []
    for prefix, count in (("pose", 25), ("hand1", 21), ("hand2", 21)):
        x = np.asarray(row[f"{prefix}_x"], dtype=np.float32)
        y = np.asarray(row[f"{prefix}_y"], dtype=np.float32)
        if x.ndim != 2 or y.ndim != 2 or x.shape != y.shape or x.shape[1] != count:
            raise ValueError(f"Unexpected {prefix} x/y shapes: {x.shape} / {y.shape}")
        x = _interpolate_columns(x) * 1920.0
        y = _interpolate_columns(y) * 1080.0
        part = np.stack([x, y], axis=-1).reshape(x.shape[0], count * 2)
        parts.append(part.astype(np.float32))
    seq = np.concatenate(parts, axis=1).astype(np.float32)
    if seq.shape[1] != 134 or not np.isfinite(seq).all():
        raise ValueError(f"Official preprocessing yielded invalid sequence: {seq.shape}")
    if seq.shape[0] > 169:
        raise ValueError(f"Clip has {seq.shape[0]} frames; official evaluation pads to 169 and does not handle overlength clips")
    padded = np.zeros((169, 134), dtype=np.float32)
    padded[:seq.shape[0]] = seq
    return seq, padded, row


def _top5(model, padded, labels, torch, seed=None):
    from isl_recognition.labels import invert_label_map
    if seed is not None:
        torch.manual_seed(seed)
    result = model.predict(padded)
    inv = invert_label_map(labels)
    return {
        "top1_label": inv[int(result["top5_indices"][0])],
        "top1_confidence": float(result["top5_probabilities"][0]),
        "top5": [
            {"rank": i + 1, "label": inv[int(idx)], "confidence": float(prob)}
            for i, (idx, prob) in enumerate(zip(result["top5_indices"], result["top5_probabilities"]))
        ],
    }



def install_pose_compatibility(mp):
    """Ensure legacy INCLUDE upper_body_only=True works with current MediaPipe.

    Returns a mode label. Current MediaPipe compatibility slices the first 25
    landmarks from full-pose output for shape compatibility; this is explicitly
    not claimed to reproduce the historic dedicated upper-body pose model.
    """
    pose_cls = mp.solutions.pose.Pose
    try:
        pose_params = inspect.signature(pose_cls).parameters
    except (TypeError, ValueError):
        pose_params = {}
    if "upper_body_only" in pose_params:
        return "native_legacy_upper_body_only_supported"

    class _PoseCompatAdapter:
        def __init__(self, *args, **kwargs):
            self._upper_body_only = bool(kwargs.pop("upper_body_only", False))
            self._pose = pose_cls(*args, **kwargs)

        def process(self, image):
            result = self._pose.process(image)
            if not self._upper_body_only:
                return result
            points = getattr(getattr(result, "pose_landmarks", None), "landmark", None)
            if points is None:
                return SimpleNamespace(pose_landmarks=None)
            return SimpleNamespace(
                pose_landmarks=SimpleNamespace(landmark=list(points[:25]))
            )

        def close(self):
            return self._pose.close()

    mp.solutions.pose.Pose = _PoseCompatAdapter
    return "compat_full_pose_first_25_points_not_exact_legacy_model"

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--resize-width", type=int, default=640,
                        help="App-path resize width; set 0 for native input resolution")
    parser.add_argument("--jpeg-quality", type=int, default=90,
                        help="JPEG quality used by the app-path diagnostic (1-100)")
    parser.add_argument("--repeat", type=int, default=3,
                        help="Number of consecutive same-input predictions for dropout-stability probe")
    parser.add_argument("--output", type=Path, default=Path("reference_parity_diagnostic.json"))
    args = parser.parse_args()
    if args.repeat < 2:
        parser.error("--repeat must be at least 2")
    if args.resize_width < 0:
        parser.error("--resize-width must be >= 0")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be between 1 and 100")
    video = args.video.resolve()
    if not video.is_file():
        print(f"ERROR: video not found: {video}", file=sys.stderr)
        return 2

    try:
        import cv2
        import numpy as np
        import torch
        from isl_recognition.app_config import Settings, resolve_label_map
        from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
        from isl_recognition.labels import load_label_map
        from isl_recognition.model import INCLUDETransformerSmall
        from isl_recognition.preprocess import preprocess_feature_sequence
    except Exception as exc:
        print(f"ERROR: could not import project runtime: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    settings = Settings()
    settings.validate()
    repo = Path(settings.ai4bharat_repo).resolve()
    checkpoint = Path(settings.checkpoint).resolve()
    label_path = resolve_label_map(settings)
    if not repo.is_dir() or not checkpoint.is_file() or not label_path or not Path(label_path).is_file():
        print("ERROR: AI4BHARAT_REPO, checkpoint, or INCLUDE-50 label map cannot be resolved from .env", file=sys.stderr)
        return 3
    labels = load_label_map(Path(label_path))
    expected = args.expected.strip().lower().replace(" ", "")
    if expected not in labels:
        print(f"ERROR: expected label '{expected}' is not in the configured INCLUDE-50 label map", file=sys.stderr)
        return 2

    # Native official generator expects slash separators and obtains the label from
    # the parent directory; use a temporary folder with the right directory shape.
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    try:
        import mediapipe as mp
        from generate_keypoints import process_video as official_process_video
    except Exception as exc:
        print(f"ERROR: could not import official AI4Bharat generate_keypoints.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    # The upstream generator uses upper_body_only=True, removed from current
    # MediaPipe. Apply a local compatibility adapter to this diagnostic process.
    pose_compatibility_mode = install_pose_compatibility(mp)

    started = time.perf_counter()
    app_features = []
    app_latencies = []
    app_coverage = {"pose": 0, "any_hand": 0, "hand1": 0, "hand2": 0, "both_hands": 0}
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"ERROR: OpenCV could not open {video}", file=sys.stderr)
        return 4
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    source_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    extractor = MediaPipeFeatureExtractor()
    session = None
    try:
        session = extractor.start_session()
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if args.resize_width and frame.shape[1] != args.resize_width:
                h, w = frame.shape[:2]
                frame = cv2.resize(frame, (args.resize_width, max(1, round(h * args.resize_width / w))), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])
            if not ok:
                continue
            t0 = time.perf_counter()
            result = extractor.extract(encoded.tobytes(), session)
            app_latencies.append((time.perf_counter() - t0) * 1000.0)
            app_features.append(np.asarray(result.features, dtype=np.float32))
            app_coverage["pose"] += int(bool(result.pose_found))
            count = int(getattr(result, "hand_count", 0))
            app_coverage["any_hand"] += int(count > 0)
            h1, h2 = bool(getattr(result, "hand1_found", False)), bool(getattr(result, "hand2_found", False))
            app_coverage["hand1"] += int(h1)
            app_coverage["hand2"] += int(h2)
            app_coverage["both_hands"] += int(h1 and h2)
    finally:
        cap.release()
        extractor.close_session(session)
    app_wall_ms = (time.perf_counter() - started) * 1000.0
    if not app_features:
        print("ERROR: no app-path frames were extracted", file=sys.stderr)
        return 4
    app_raw, app_padded = preprocess_feature_sequence(np.stack(app_features), 169, 134)

    with tempfile.TemporaryDirectory(prefix="isl_official_parity_") as tmp:
        tmp_path = Path(tmp)
        # A slash-normalized source path gives the official helper its expected label.
        normalized_video = str(video).replace("\\", "/")
        keypoints_dir = tmp_path / "keypoints"
        keypoints_dir.mkdir()
        official_process_video(normalized_video, str(keypoints_dir))
        records = list(keypoints_dir.glob("*.json"))
        if len(records) != 1:
            raise RuntimeError(f"Official generator produced {len(records)} JSON records, expected 1")
        official_raw, official_padded, official_row = official_json_to_padded(records[0])

    model = INCLUDETransformerSmall(repo, checkpoint)
    # Reset the random seed before each branch so the same functional-dropout mask
    # is used for a fair feature-input comparison if upstream dropout is unconditional.
    seed_for_comparison = 20261029
    official_prediction = _top5(model, official_padded, labels, torch, seed_for_comparison)
    app_prediction = _top5(model, app_padded, labels, torch, seed_for_comparison)
    # Consecutive predictions on exactly the same tensor reveal stochastic inference.
    repeated = [_top5(model, app_padded, labels, torch) for _ in range(args.repeat)]

    upstream_model = repo / "models" / "transformer.py"
    source = upstream_model.read_text(encoding="utf-8", errors="replace") if upstream_model.is_file() else ""
    unconditional_dropout = "F.dropout(x, p=0.2)" in source.replace(" ", "") or "F.dropout(x,p=0.2)" in source.replace(" ", "")
    # Also handle spacing variations without misclassifying a training-aware call.
    import re
    matches = re.findall(r"F\.dropout\s*\(\s*x\s*,\s*p\s*=\s*0\.2\s*([^)]*)\)", source)
    unconditional_dropout = any("training" not in args_text for args_text in matches)

    common_frames = min(len(app_raw), len(official_raw))
    diff = np.abs(app_raw[:common_frames] - official_raw[:common_frames])
    denom = max(1, len(app_features))
    report = {
        "test_type": "single-reference-app-vs-official-parity-diagnostic-not-accuracy-evaluation",
        "video": str(video),
        "expected_label": expected,
        "source_fps": source_fps,
        "declared_source_frames": source_frames,
        "app_frames_processed": len(app_features),
        "official_frames_processed": int(official_row.get("n_frames", len(official_raw))),
        "app_resize_width": args.resize_width if args.resize_width else "native",
        "app_jpeg_quality": args.jpeg_quality,
        "app_input_policy": "native source resolution + JPEG encode/decode at configured quality; use resize-width=0 and jpeg-quality=90 to match official validation evaluator",
        "official_extractor_resolution": "native source frames",
        "model_input_shape_both": [169, 134],
        "feature_parity": {
            "compared_frames": common_frames,
            "mean_absolute_feature_difference": float(diff.mean()) if diff.size else None,
            "p95_absolute_feature_difference": float(np.percentile(diff, 95)) if diff.size else None,
            "max_absolute_feature_difference": float(diff.max()) if diff.size else None,
            "exactly_equal": bool(np.allclose(app_raw[:common_frames], official_raw[:common_frames], rtol=1e-5, atol=1e-4)) if common_frames else False,
        },
        "app_extractor": {
            "mean_frame_latency_ms": float(np.mean(app_latencies)) if app_latencies else None,
            "median_frame_latency_ms": float(median(app_latencies)) if app_latencies else None,
            "wall_time_ms": app_wall_ms,
            "coverage": {k: v / denom for k, v in app_coverage.items()},
        },
        "official_generator": {
            "source": str(repo / "generate_keypoints.py"),
            "pose_configuration_requested_by_source": "upper_body_only=True",
            "pose_compatibility_mode": pose_compatibility_mode,
            "uses_resize": False,
            "compatibility_warning": (
                "Current MediaPipe lacks the legacy upper_body_only option. The diagnostic adapter slices the first 25 points from current full-pose output; this is a shape-compatible approximation, not an exact reproduction of the historical upper-body model."
                if pose_compatibility_mode == "compat_full_pose_first_25_points_not_exact_legacy_model"
                else None
            ),
        },
        "checkpoint": str(checkpoint),
        "model_code_path": str(upstream_model),
        "unconditional_functional_dropout_detected": unconditional_dropout,
        "official_pipeline_prediction_same_rng_seed": official_prediction,
        "app_pipeline_prediction_same_rng_seed": app_prediction,
        "same_app_input_repeated_predictions": repeated,
        "same_app_input_top1_labels": [r["top1_label"] for r in repeated],
        "same_app_input_confidences": [r["top1_confidence"] for r in repeated],
        "confidence_threshold": settings.confidence_threshold,
        "note": "One clip diagnoses pipeline differences only; it cannot estimate dataset accuracy or calibrate confidence.",
    }
    out = args.output if args.output.is_absolute() else (ROOT / args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
