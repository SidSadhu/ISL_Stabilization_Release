#!/usr/bin/env python
"""Run one local INCLUDE-50 reference clip through this project's actual pipeline.

This is a diagnostic smoke test, not a dataset accuracy benchmark. It uses the
configured checkpoint, label map, Python MediaPipe extractor, and (169, 134)
preprocessing used by the web app.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    import numpy as np
    return float(np.percentile(np.asarray(values, dtype=float), q))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Path to a local INCLUDE-50 reference video")
    parser.add_argument("--expected", required=True, help="Expected label in the canonical INCLUDE-50 map, e.g. hello")
    parser.add_argument("--resize-width", type=int, default=640,
                        help="Resize frames to this width before extraction (default: 640, matching web capture width)")
    parser.add_argument("--max-frames", type=int, default=169,
                        help="Maximum sequential frames to process (1..169; default: 169)")
    parser.add_argument("--jpeg-quality", type=int, default=92,
                        help="JPEG quality for offline frame encoding (0..100; default: 92, matching the current browser capture setting)")
    args = parser.parse_args()

    if not args.video.is_file():
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 2
    if not 1 <= args.max_frames <= 169:
        print("ERROR: --max-frames must be between 1 and 169", file=sys.stderr)
        return 2
    if args.resize_width < 0:
        print("ERROR: --resize-width must be >= 0 (0 means native resolution)", file=sys.stderr)
        return 2
    if not 0 <= args.jpeg_quality <= 100:
        print("ERROR: --jpeg-quality must be between 0 and 100", file=sys.stderr)
        return 2

    try:
        import cv2
        import numpy as np
        from isl_recognition.app_config import Settings, resolve_label_map
        from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
        from isl_recognition.labels import invert_label_map, load_label_map
        from isl_recognition.model import INCLUDETransformerSmall
        from isl_recognition.preprocess import preprocess_feature_sequence
    except Exception as exc:
        print(f"ERROR: could not import project runtime dependencies: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    settings = Settings()
    try:
        settings.validate()
    except Exception as exc:
        print(f"ERROR: invalid project settings: {exc}", file=sys.stderr)
        return 3
    if not settings.ai4bharat_repo or not settings.checkpoint:
        print("ERROR: .env must configure AI4BHARAT_REPO and ISL_CHECKPOINT", file=sys.stderr)
        return 3
    label_path = resolve_label_map(settings)
    if not label_path or not label_path.is_file():
        print("ERROR: official INCLUDE-50 label map not found; check AI4BHARAT_REPO / ISL_LABEL_MAP", file=sys.stderr)
        return 3
    try:
        labels = load_label_map(label_path)
    except Exception as exc:
        print(f"ERROR: label map validation failed: {exc}", file=sys.stderr)
        return 3
    expected = args.expected.strip().lower().replace(" ", "")
    if expected not in labels:
        print(f"ERROR: expected label '{args.expected}' not in canonical label map. Example labels: {', '.join(sorted(labels)[:15])}", file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print(f"ERROR: OpenCV could not open video: {args.video}", file=sys.stderr)
        return 4
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    source_duration_s = declared_frames / source_fps if source_fps > 0 and declared_frames > 0 else None

    extractor = MediaPipeFeatureExtractor()
    session = None
    features: list[np.ndarray] = []
    mp_ms: list[float] = []
    pose_hits = hand_hits = hand1_hits = hand2_hits = both_hits = 0
    started = time.perf_counter()
    try:
        setup_start = time.perf_counter()
        session = extractor.start_session()
        session_setup_ms = (time.perf_counter() - setup_start) * 1000.0
        while len(features) < args.max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            source_h, source_w = frame.shape[:2]
            if args.resize_width and source_w != args.resize_width:
                new_height = max(1, round(source_h * args.resize_width / source_w))
                frame = cv2.resize(frame, (args.resize_width, new_height), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality])
            if not ok:
                print(f"WARNING: frame {len(features)} could not be JPEG-encoded; skipped", file=sys.stderr)
                continue
            frame_started = time.perf_counter()
            result = extractor.extract(encoded.tobytes(), session)
            mp_ms.append((time.perf_counter() - frame_started) * 1000.0)
            vec = np.asarray(result.features, dtype=np.float32)
            if vec.shape != (134,) or np.isinf(vec).any():
                raise RuntimeError(f"Extractor returned invalid feature shape/content: {vec.shape}")
            features.append(vec.copy())
            pose_hits += int(bool(result.pose_found))
            any_hand = int(getattr(result, "hand_count", 0)) > 0
            h1 = bool(getattr(result, "hand1_found", False))
            h2 = bool(getattr(result, "hand2_found", False))
            hand_hits += int(any_hand)
            hand1_hits += int(h1)
            hand2_hits += int(h2)
            both_hits += int(h1 and h2)
    except Exception as exc:
        print(f"ERROR: feature extraction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    finally:
        cap.release()
        extractor.close_session(session)
    if not features:
        print("ERROR: no readable frames were extracted from the video", file=sys.stderr)
        return 4

    feature_extract_total_ms = (time.perf_counter() - started) * 1000.0
    sequence = np.stack(features, axis=0).astype(np.float32)
    raw, padded = preprocess_feature_sequence(sequence, max_frame_len=169, feature_dim=134)

    try:
        model_start = time.perf_counter()
        model = INCLUDETransformerSmall(settings.ai4bharat_repo, settings.checkpoint)
        load_ms = (time.perf_counter() - model_start) * 1000.0
        infer_start = time.perf_counter()
        prediction = model.predict(padded)
        inference_ms = (time.perf_counter() - infer_start) * 1000.0
    except Exception as exc:
        print(f"ERROR: model load/prediction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 6

    index_to_label = invert_label_map(labels)
    top5 = [
        {"rank": i + 1, "label": index_to_label[int(idx)], "confidence": float(prob)}
        for i, (idx, prob) in enumerate(zip(prediction["top5_indices"], prediction["top5_probabilities"]))
    ]
    top1 = top5[0]["label"]
    expected_top5 = any(item["label"] == expected for item in top5)
    denominator = len(features)
    report = {
        "test_type": "single-reference-pipeline-smoke-test-not-accuracy-evaluation",
        "video": str(args.video.resolve()),
        "expected_label": expected,
        "source_fps": source_fps,
        "declared_source_frames": declared_frames,
        "declared_source_duration_seconds": source_duration_s,
        "resize_width": args.resize_width if args.resize_width else "native",
        "jpeg_quality": args.jpeg_quality,
        "frames_processed": denominator,
        "max_frame_limit": args.max_frames,
        "source_was_truncated_by_frame_limit": bool(declared_frames > args.max_frames),
        "model_input_shape": list(padded.shape),
        "model_device": prediction.get("device", "unknown"),
        "pose_coverage": pose_hits / denominator,
        "any_hand_coverage": hand_hits / denominator,
        "hand_slot_1_coverage": hand1_hits / denominator,
        "hand_slot_2_coverage": hand2_hits / denominator,
        "both_hands_coverage": both_hits / denominator,
        "extractor_session_setup_ms": session_setup_ms,
        "extractor_frame_latency_median_ms": float(median(mp_ms)) if mp_ms else None,
        "extractor_frame_latency_p95_ms": percentile(mp_ms, 95),
        "feature_extraction_wall_time_ms": feature_extract_total_ms,
        "model_load_ms": load_ms,
        "inference_ms": inference_ms,
        "top1_label": top1,
        "top1_confidence": float(prediction["confidence"]),
        "expected_is_top1": top1 == expected,
        "expected_is_in_top5": expected_top5,
        "top5": top5,
        "confidence_threshold": settings.confidence_threshold,
        "would_pass_confidence_threshold": float(prediction["confidence"]) >= settings.confidence_threshold,
    }
    print(json.dumps(report, indent=2))
    if report["source_was_truncated_by_frame_limit"]:
        print("NOTE: processed only the first frames up to the requested cap; this is not a full-video evaluation.", file=sys.stderr)
    print("NOTE: one clip checks pipeline consistency only; it cannot establish overall recognition accuracy.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
