#!/usr/bin/env python
"""Benchmark local INCLUDE-50 reference clips through the app's actual pipeline.

This is a smoke/diagnostic benchmark, not a substitute for official held-out test
metrics. It loads the model once, processes one or more locally downloaded videos
using the current project's MediaPipeFeatureExtractor, pads to (169, 134), and
reports top-1/top-5 accuracy, coverage, latency, and exploratory confidence-threshold
sweeps. Expected labels are inferred from class-folder names such as "48. Hello".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def normalize_name(value: str) -> str:
    """Normalize folders like '48. Hello' to 'hello' for canonical-label matching."""
    value = re.sub(r"^\s*\d+\s*[._\-)]*\s*", "", value.strip())
    return re.sub(r"[^a-z0-9]", "", value.lower())


def infer_expected_label(video: Path, label_map: dict[str, int]) -> str | None:
    normalized_map = {normalize_name(label): label for label in label_map}
    for part in reversed(video.parent.parts):
        normalized = normalize_name(part)
        if normalized in normalized_map:
            return normalized_map[normalized]
    return None


def threshold_sweep(rows: list[dict], thresholds: list[float]) -> list[dict]:
    out = []
    for threshold in thresholds:
        accepted = [r for r in rows if r.get("top1_confidence") is not None and r["top1_confidence"] >= threshold]
        out.append({
            "threshold": threshold,
            "accepted_count": len(accepted),
            "accepted_rate": len(accepted) / len(rows) if rows else None,
            "top1_accuracy_among_accepted": (
                sum(bool(r["top1_correct"]) for r in accepted) / len(accepted) if accepted else None
            ),
            "accepted_correct_count": sum(bool(r["top1_correct"]) for r in accepted),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-root", type=Path, required=True, help="Root containing class folders, e.g. C:/AIAssets/INCLUDE_videos")
    parser.add_argument("--resize-width", type=int, default=640, help="Resize frames to this width; 0 means native. Default 640 to mirror the current app diagnostic path.")
    parser.add_argument("--samples-per-class", type=int, default=1, help="Maximum local clips per class; default 1")
    parser.add_argument("--max-clips", type=int, default=0, help="Maximum total clips (0 means all selected class clips)")
    parser.add_argument("--max-frames", type=int, default=169, help="Maximum sequential frames to extract per clip (1..169)")
    parser.add_argument("--output", type=Path, default=Path("reference_gallery_benchmark.json"))
    args = parser.parse_args()
    if args.resize_width < 0:
        parser.error("--resize-width must be >= 0")
    if args.samples_per_class < 1:
        parser.error("--samples-per-class must be >= 1")
    if not 1 <= args.max_frames <= 169:
        parser.error("--max-frames must be between 1 and 169")
    root = args.video_root.expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: video root not found: {root}", file=sys.stderr)
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
        print(f"ERROR: project imports failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    settings = Settings()
    try:
        settings.validate()
        label_path = resolve_label_map(settings)
        if not label_path or not Path(label_path).is_file():
            raise FileNotFoundError("Canonical INCLUDE-50 label map not found; check .env")
        labels = load_label_map(Path(label_path))
        inverse_labels = invert_label_map(labels)
        if not settings.ai4bharat_repo or not settings.checkpoint:
            raise FileNotFoundError(".env must define AI4BHARAT_REPO and ISL_CHECKPOINT")
        repo = Path(settings.ai4bharat_repo).resolve()
        checkpoint = Path(settings.checkpoint).resolve()
        if not repo.is_dir() or not checkpoint.is_file():
            raise FileNotFoundError(f"Repository/checkpoint missing: {repo} / {checkpoint}")
    except Exception as exc:
        print(f"ERROR: project configuration invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    all_videos = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
    mapped: dict[str, list[Path]] = defaultdict(list)
    unmapped: list[str] = []
    for video in all_videos:
        label = infer_expected_label(video, labels)
        if label is None:
            unmapped.append(str(video))
        else:
            mapped[label].append(video)
    selected: list[tuple[str, Path]] = []
    for label in sorted(mapped):
        for path in mapped[label][:args.samples_per_class]:
            selected.append((label, path))
    selected.sort(key=lambda pair: (pair[0], str(pair[1]).lower()))
    if args.max_clips > 0:
        selected = selected[:args.max_clips]
    if not selected:
        print("ERROR: no videos mapped to canonical INCLUDE-50 class folder names. Expected folders such as '48. Hello'.", file=sys.stderr)
        print(f"Found {len(all_videos)} video files; {len(unmapped)} had no matching class folder.", file=sys.stderr)
        return 4

    print(f"Found {len(all_videos)} video files; selected {len(selected)} across {len(set(label for label, _ in selected))} classes.")
    print("Loading checkpoint once. This benchmark may take several minutes on CPU.", flush=True)
    model_started = time.perf_counter()
    try:
        model = INCLUDETransformerSmall(repo, checkpoint)
    except Exception as exc:
        print(f"ERROR: model initialization failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    model_load_ms = (time.perf_counter() - model_started) * 1000.0
    extractor = MediaPipeFeatureExtractor()
    results: list[dict] = []
    failed: list[dict] = []
    total_started = time.perf_counter()

    for idx, (expected, video) in enumerate(selected, start=1):
        print(f"[{idx}/{len(selected)}] {expected}: {video.name}", flush=True)
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            failed.append({"video": str(video), "expected_label": expected, "error": "OpenCV could not open video"})
            continue
        source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        features = []
        latencies_ms = []
        pose_hits = any_hand_hits = h1_hits = h2_hits = both_hits = 0
        session = None
        extract_started = time.perf_counter()
        try:
            session = extractor.start_session()
            while len(features) < args.max_frames:
                ok, frame = cap.read()
                if not ok:
                    break
                if args.resize_width and frame.shape[1] != args.resize_width:
                    height, width = frame.shape[:2]
                    new_height = max(1, round(height * args.resize_width / width))
                    frame = cv2.resize(frame, (args.resize_width, new_height), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                if not ok:
                    continue
                frame_started = time.perf_counter()
                extracted = extractor.extract(encoded.tobytes(), session)
                latencies_ms.append((time.perf_counter() - frame_started) * 1000.0)
                feature = np.asarray(extracted.features, dtype=np.float32)
                if feature.shape != (134,) or np.isinf(feature).any():
                    raise ValueError(f"Invalid feature vector: {feature.shape}")
                features.append(feature.copy())
                pose_hits += int(bool(extracted.pose_found))
                hand_count = int(getattr(extracted, "hand_count", 0))
                h1 = bool(getattr(extracted, "hand1_found", False))
                h2 = bool(getattr(extracted, "hand2_found", False))
                any_hand_hits += int(hand_count > 0)
                h1_hits += int(h1)
                h2_hits += int(h2)
                both_hits += int(h1 and h2)
        except Exception as exc:
            failed.append({"video": str(video), "expected_label": expected, "error": f"{type(exc).__name__}: {exc}"})
            continue
        finally:
            cap.release()
            extractor.close_session(session)
        extraction_ms = (time.perf_counter() - extract_started) * 1000.0
        if not features:
            failed.append({"video": str(video), "expected_label": expected, "error": "No readable frames"})
            continue
        sequence = np.stack(features).astype(np.float32)
        raw, padded = preprocess_feature_sequence(sequence, max_frame_len=169, feature_dim=134)
        infer_started = time.perf_counter()
        try:
            prediction = model.predict(padded)
        except Exception as exc:
            failed.append({"video": str(video), "expected_label": expected, "error": f"inference {type(exc).__name__}: {exc}"})
            continue
        inference_ms = (time.perf_counter() - infer_started) * 1000.0
        top5 = [
            {"rank": rank + 1, "label": inverse_labels[int(label_idx)], "confidence": float(prob)}
            for rank, (label_idx, prob) in enumerate(zip(prediction["top5_indices"], prediction["top5_probabilities"]))
        ]
        top1_label = top5[0]["label"]
        frame_count = len(features)
        row = {
            "video": str(video), "class": expected,
            "source_fps": source_fps, "declared_source_frames": declared_frames,
            "frames_processed": frame_count,
            "clip_duration_seconds": frame_count / source_fps if source_fps > 0 else None,
            "resize_width": args.resize_width if args.resize_width else "native",
            "pose_coverage": pose_hits / frame_count,
            "any_hand_coverage": any_hand_hits / frame_count,
            "hand_slot_1_coverage": h1_hits / frame_count,
            "hand_slot_2_coverage": h2_hits / frame_count,
            "both_hands_coverage": both_hits / frame_count,
            "extractor_latency_median_ms": median(latencies_ms) if latencies_ms else None,
            "extractor_latency_p95_ms": float(np.percentile(latencies_ms, 95)) if latencies_ms else None,
            "feature_extraction_wall_ms": extraction_ms,
            "inference_ms": inference_ms,
            "top1_label": top1_label,
            "top1_confidence": float(prediction["confidence"]),
            "top1_correct": top1_label == expected,
            "top5_correct": any(item["label"] == expected for item in top5),
            "expected_rank_in_top5": next((item["rank"] for item in top5 if item["label"] == expected), None),
            "top5": top5,
        }
        results.append(row)
        print(f"    top1={top1_label} ({row['top1_confidence']:.3f}) expected={'OK' if row['top1_correct'] else expected} extract={extraction_ms/1000:.1f}s", flush=True)

    elapsed_ms = (time.perf_counter() - total_started) * 1000.0
    valid = len(results)
    summary = {
        "test_type": "local-reference-gallery-smoke-benchmark-not-official-test-accuracy",
        "video_root": str(root),
        "resize_width": args.resize_width if args.resize_width else "native",
        "model_device": str(model.device),
        "model_load_ms": model_load_ms,
        "clips_discovered": len(all_videos),
        "clips_selected": len(selected),
        "clips_evaluated": valid,
        "clips_failed": len(failed),
        "classes_evaluated": len({row['class'] for row in results}),
        "top1_accuracy": sum(row["top1_correct"] for row in results) / valid if valid else None,
        "top5_accuracy": sum(row["top5_correct"] for row in results) / valid if valid else None,
        "mean_top1_confidence": mean(row["top1_confidence"] for row in results) if valid else None,
        "median_top1_confidence": median(row["top1_confidence"] for row in results) if valid else None,
        "mean_confidence_when_top1_correct": mean(row["top1_confidence"] for row in results if row["top1_correct"]) if any(r["top1_correct"] for r in results) else None,
        "mean_confidence_when_top1_incorrect": mean(row["top1_confidence"] for row in results if not row["top1_correct"]) if any(not r["top1_correct"] for r in results) else None,
        "mean_extraction_wall_seconds_per_clip": mean(row["feature_extraction_wall_ms"] for row in results) / 1000.0 if valid else None,
        "mean_inference_ms_per_clip": mean(row["inference_ms"] for row in results) if valid else None,
        "threshold_sweep_exploratory_only": threshold_sweep(results, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]),
        "unmapped_video_count": len(unmapped),
        "unmapped_video_examples": unmapped[:20],
        "failed": failed,
        "clips": results,
        "elapsed_seconds": elapsed_ms / 1000.0,
        "limitations": [
            "This uses the app's MediaPipe extractor and the currently configured pretrained model.",
            "It is a local-gallery smoke benchmark, not an official held-out test evaluation.",
            "One clip per class gives weak class coverage and cannot establish generalization to live webcam signers.",
            "Threshold sweep is descriptive only; do not choose a production confidence threshold from this run alone.",
        ],
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n========== SUMMARY ==========")
    print(json.dumps({k: summary[k] for k in ["clips_discovered", "clips_selected", "clips_evaluated", "clips_failed", "classes_evaluated", "top1_accuracy", "top5_accuracy", "mean_top1_confidence", "median_top1_confidence", "mean_extraction_wall_seconds_per_clip", "threshold_sweep_exploratory_only", "elapsed_seconds"]}, indent=2))
    print(f"\nFull report saved to: {output}")
    return 0 if valid else 5


if __name__ == "__main__":
    raise SystemExit(main())
