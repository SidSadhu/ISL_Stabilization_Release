#!/usr/bin/env python
"""Compare the current browser-like resize/JPEG transport against lossless paths.

This is an additive, read-only diagnostic. It does not modify the application,
model, threshold, checkpoint, validation manifest, or IoT settings.

Variants:
  1. app_direct_native: original OpenCV-decoded source frames, no resize/encode.
  2. resized_640_png: width-limited resize + lossless PNG encode/decode.
  3. resized_640_jpeg_q92: width-limited resize + JPEG quality 92.
  4. upstream_original: pinned AI4Bharat generator on original source video.

The OpenCV resize (INTER_LINEAR) is an approximation of browser canvas drawImage.
It isolates resize and compression effects offline, but is not a byte-for-byte
reproduction of Chrome's canvas resampling. The q92 branch matches the current production JPEG quality. The obsolete q72 branch is deliberately excluded from this campaign; OpenCV resize/encoding remains an approximation of browser Canvas.
"""
from __future__ import annotations

import argparse
import json
import inspect
import importlib
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

# Reuse the already-installed, audited parity helpers from the previous patch.
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    from diagnose_feature_pipeline_stages import (
        _install_pose_compatibility,
        _interpolate_columns,
        _official_arrays,
        _predict,
        compare_feature_sequences,
        presence_disagreement,
        presence_summary,
    )
except Exception as exc:  # pragma: no cover - exercised in user's project env
    raise RuntimeError(
        "Install/run the existing Feature-Stage Parity Diagnostics patch first; "
        "scripts/diagnose_feature_pipeline_stages.py is required."
    ) from exc



def _install_upstream_missing_pose_guard(generator_module) -> dict[str, int]:
    """Make the pinned upstream generator tolerate an absent Pose result.

    AI4Bharat's original process_pose_keypoints() dereferences
    results.pose_landmarks.landmark without checking for None. The official
    dataset pipeline represents missing observations as NaN slots and
    interpolates them later, so returning 25 NaNs here preserves the expected
    25-point/134-D feature layout and the upstream dataset's missing-data
    semantics. When pose is absent, the upstream one-hand wrist assignment sees
    NaN wrists and leaves detector order unchanged, matching the app's policy
    of not assigning a hand without pose wrists.

    The pinned upstream checkout is never modified; this is a per-process
    diagnostic compatibility shim only.
    """
    original = generator_module.process_pose_keypoints
    stats = {"missing_pose_frame_count": 0}

    def guarded_process_pose_keypoints(results):
        pose_landmarks = getattr(results, "pose_landmarks", None)
        if pose_landmarks is None:
            stats["missing_pose_frame_count"] += 1
            missing = [float("nan")] * 25
            return missing.copy(), missing.copy()
        return original(results)

    generator_module.process_pose_keypoints = guarded_process_pose_keypoints
    return stats

def resized_dimensions(width: int, height: int, target_width: int) -> tuple[int, int]:
    """Match app.js width=640 and proportional height, with integer rounding."""
    if width < 1 or height < 1 or target_width < 1:
        raise ValueError("width, height and target_width must be positive")
    return target_width, max(1, int(np.floor(target_width * height / width + 0.5)))


def encode_frame_variants(frame_bgr: np.ndarray, cv2, target_width: int, qualities: tuple[int, ...]):
    """Resize one BGR frame and encode PNG plus requested JPEG qualities."""
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("frame_bgr must be a non-empty HxWx3 image")
    h, w = frame_bgr.shape[:2]
    out_w, out_h = resized_dimensions(w, h, target_width)
    resized = cv2.resize(frame_bgr, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

    ok_png, png = cv2.imencode(".png", resized)
    if not ok_png:
        raise RuntimeError("PNG encoding failed")
    variants: dict[str, bytes] = {"resized_640_png": png.tobytes()}
    for quality in qualities:
        ok_jpeg, jpeg = cv2.imencode(
            ".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        )
        if not ok_jpeg:
            raise RuntimeError(f"JPEG encoding failed at quality {quality}")
        variants[f"resized_640_jpeg_q{quality}"] = jpeg.tobytes()

    pixel_mae: dict[str, float] = {}
    for name, payload in variants.items():
        decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None or decoded.shape != resized.shape:
            raise RuntimeError(f"Failed to decode {name} to the expected resized frame shape")
        pixel_mae[name] = float(np.mean(np.abs(resized.astype(np.float32) - decoded.astype(np.float32))))
    return resized, variants, pixel_mae


def _extract_sequence(payloads: list[bytes], extractor) -> tuple[np.ndarray, list[float], float]:
    """Run a fresh sequential MediaPipe session for one encoded frame variant."""
    feature_rows: list[np.ndarray] = []
    latencies_ms: list[float] = []
    session = extractor.start_session()
    started = time.perf_counter()
    try:
        for payload in payloads:
            frame_started = time.perf_counter()
            item = extractor.extract(payload, session)
            latencies_ms.append((time.perf_counter() - frame_started) * 1000.0)
            feature_rows.append(np.asarray(item.features, dtype=np.float32))
    finally:
        extractor.close_session(session)
    return np.stack(feature_rows), latencies_ms, time.perf_counter() - started


def _direct_sequence(video: Path, mp, cv2, target_width: int = 640, mode: str = "verified_one_hand_fix"):
    """Run current app landmark mapping on original decoded source frames."""
    from isl_recognition.landmarks import landmark_results_to_feature_frame
    from isl_recognition.preprocess import make_scaled_frame_feature

    hands = mp.solutions.hands.Hands(
        static_image_mode=False,
        max_num_hands=2,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    pose = mp.solutions.pose.Pose(
        static_image_mode=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
        model_complexity=1,
    )
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        hands.close()
        pose.close()
        raise RuntimeError(f"OpenCV could not open {video}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    features: list[np.ndarray] = []
    started = time.perf_counter()
    latencies: list[float] = []
    payloads: dict[str, list[bytes]] = {
        "resized_640_png": [],
        "resized_640_jpeg_q92": [],
    }
    pixel_mae: dict[str, list[float]] = {k: [] for k in payloads}
    resized_shapes: set[tuple[int, int]] = set()
    original_shapes: set[tuple[int, int]] = set()

    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            original_shapes.add((int(bgr.shape[1]), int(bgr.shape[0])))
            frame_started = time.perf_counter()
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            hand_result = hands.process(rgb)
            pose_result = pose.process(rgb)
            landmarks = landmark_results_to_feature_frame(hand_result, pose_result, mode)
            vector = make_scaled_frame_feature(
                landmarks.pose_x, landmarks.pose_y,
                landmarks.hand1_x, landmarks.hand1_y,
                landmarks.hand2_x, landmarks.hand2_y,
                allow_nan=True,
            )
            features.append(vector.astype(np.float32))
            latencies.append((time.perf_counter() - frame_started) * 1000.0)

            _, encoded, pixel_diffs = encode_frame_variants(
                bgr, cv2, target_width, (92,)
            )
            for key, value in encoded.items():
                payloads[key].append(value)
                pixel_mae[key].append(pixel_diffs[key])
            resized_shape = cv2.imdecode(
                np.frombuffer(encoded["resized_640_png"], dtype=np.uint8), cv2.IMREAD_COLOR
            ).shape
            resized_shapes.add((int(resized_shape[1]), int(resized_shape[0])))
    finally:
        cap.release()
        hands.close()
        pose.close()

    if not features:
        raise RuntimeError("No source frames could be decoded")
    return (
        np.stack(features), payloads, pixel_mae, fps, declared_frames,
        latencies, time.perf_counter() - started,
        sorted(original_shapes), sorted(resized_shapes),
    )


def _pad(sequence: np.ndarray) -> np.ndarray:
    if len(sequence) > 169:
        raise ValueError(f"Video has {len(sequence)} frames, above checkpoint length 169")
    padded = np.zeros((169, 134), dtype=np.float32)
    padded[:len(sequence)] = sequence
    return padded


def diagnose(video: Path, expected: str, target_width: int = 640) -> dict[str, Any]:
    import cv2
    import mediapipe as mp
    import torch
    from isl_recognition.app_config import Settings, resolve_label_map
    from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
    from isl_recognition.labels import load_label_map
    from isl_recognition.model import INCLUDETransformerSmall

    settings = Settings()
    settings.validate()
    repo = Path(settings.ai4bharat_repo).resolve()
    checkpoint = Path(settings.checkpoint).resolve()
    label_path = resolve_label_map(settings)
    if not repo.is_dir() or not checkpoint.is_file() or not label_path or not Path(label_path).is_file():
        raise RuntimeError("AI4BHARAT_REPO/checkpoint/label map could not be resolved")
    labels = load_label_map(Path(label_path))
    expected_norm = expected.strip().lower().replace(" ", "")
    if expected_norm not in labels:
        raise ValueError(f"Expected label {expected_norm!r} not found in INCLUDE-50 label map")

    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    pose_mode = _install_pose_compatibility(mp)
    upstream_module = importlib.import_module("generate_keypoints")
    upstream_missing_pose_stats = _install_upstream_missing_pose_guard(upstream_module)
    upstream_process_video = upstream_module.process_video
    direct_raw, payloads, pixel_mae, source_fps, declared_frames, direct_latencies, direct_seconds, original_shapes, resized_shapes = (
        _direct_sequence(video, mp, cv2, target_width)
    )

    extractor = MediaPipeFeatureExtractor()
    raw_variants: dict[str, np.ndarray] = {"app_direct_native": direct_raw}
    timing_variants: dict[str, dict[str, Any]] = {}
    encoded_variant_bytes: dict[str, int] = {}
    for name, frames in payloads.items():
        raw, latencies, seconds = _extract_sequence(frames, extractor)
        raw_variants[name] = raw
        encoded_variant_bytes[name] = int(sum(len(frame) for frame in frames))
        timing_variants[name] = {
            "median_per_frame_ms": float(np.median(latencies)),
            "p95_per_frame_ms": float(np.percentile(latencies, 95)),
            "mean_per_frame_ms": float(np.mean(latencies)),
            "feature_loop_seconds": float(seconds),
        }

    with tempfile.TemporaryDirectory(prefix="isl_browser_transport_") as temp:
        output_dir = Path(temp) / "keypoints"
        output_dir.mkdir()
        upstream_process_video(str(video).replace("\\", "/"), str(output_dir))
        records = list(output_dir.glob("*.json"))
        if len(records) != 1:
            raise RuntimeError(f"Upstream generated {len(records)} JSON records; expected exactly one")
        upstream_raw = _official_arrays(json.loads(records[0].read_text(encoding="utf-8")))

    frame_counts = {key: int(len(value)) for key, value in raw_variants.items()}
    frame_counts["upstream_original"] = int(len(upstream_raw))
    if len(set(frame_counts.values())) != 1:
        raise RuntimeError(f"Frame count mismatch across feature paths: {frame_counts}")
    if len(direct_raw) > 169:
        raise RuntimeError(f"Video has {len(direct_raw)} frames, above checkpoint length 169")
    raw_variants["upstream_original"] = upstream_raw

    presence = {key: presence_summary(value) for key, value in raw_variants.items()}
    sequences = {key: _interpolate_columns(value) for key, value in raw_variants.items()}
    padded = {key: _pad(value) for key, value in sequences.items()}

    model = INCLUDETransformerSmall(repo, checkpoint)
    predictions = {
        key: _predict(model, value, labels, torch)
        for key, value in padded.items()
    }

    pairs = {
        "app_direct_native_vs_upstream": ("app_direct_native", "upstream_original"),
        "app_direct_native_vs_resized_png": ("app_direct_native", "resized_640_png"),
        "resized_png_vs_jpeg_q92": ("resized_640_png", "resized_640_jpeg_q92"),
        "opencv_q92_vs_upstream": ("resized_640_jpeg_q92", "upstream_original"),
    }
    comparisons = {
        name: compare_feature_sequences(sequences[left], sequences[right])
        for name, (left, right) in pairs.items()
    }
    presence_disagreements = {
        name: presence_disagreement(raw_variants[left], raw_variants[right])
        for name, (left, right) in pairs.items()
    }
    top1_agreement = {
        name: predictions[left]["top1_label"] == predictions[right]["top1_label"]
        for name, (left, right) in pairs.items()
    }

    return {
        "status": "success",
        "diagnostic_type": "browser_capture_resize_and_jpeg_transport_ablation",
        "video": str(video.resolve()),
        "expected_label": expected_norm,
        "source_fps": source_fps,
        "declared_source_frames": declared_frames,
        "frames_processed_each_path": len(direct_raw),
        "frame_counts_by_path": frame_counts,
        "model_input_shape": [169, 134],
        "opencv_canvas_transform_approximation": {
            "target_width": target_width,
            "height_policy": "round(target_width * source_height / source_width)",
            "resize_implementation": "OpenCV INTER_LINEAR (approximation of browser canvas drawImage)",
            "current_source_app_js_quality": 92,
            "historical_quality_72_included": False,
            "original_frame_dimensions_wh": original_shapes,
            "resized_frame_dimensions_wh": resized_shapes,
            "exact_browser_canvas_pixel_parity": False,
        },
        "upstream_pose_compatibility_mode": pose_mode,
        "upstream_missing_pose_policy": "missing pose frames are represented by 25 NaN x/y slots before the upstream dataset interpolation stage",
        "upstream_missing_pose_frame_count": upstream_missing_pose_stats["missing_pose_frame_count"],
        "warning": (
            "Upstream path uses a modern MediaPipe first-25-landmark compatibility adapter; "
            "not exact historical upper_body_only detector parity. Resize uses OpenCV INTER_LINEAR, "
            "not browser-native canvas drawImage; use this as a controlled approximation, then verify live browser capture."
        ),
        "encoded_payload_total_bytes": encoded_variant_bytes,
        "pixel_roundtrip_mean_absolute_error": {
            key: {
                "mean": float(np.mean(values)),
                "p95": float(np.percentile(values, 95)),
                "max": float(np.max(values)),
            }
            for key, values in pixel_mae.items()
        },
        "feature_presence_before_interpolation": presence,
        "feature_comparisons_after_interpolation": comparisons,
        "hand_slot_presence_disagreement": presence_disagreements,
        "predictions_same_seed": predictions,
        "top1_agreement_by_pair": top1_agreement,
        "timings": {
            "app_direct_native_per_frame_ms": {
                "median": float(np.median(direct_latencies)),
                "p95": float(np.percentile(direct_latencies, 95)),
                "mean": float(np.mean(direct_latencies)),
                "branch_seconds_including_video_decode_and_encode": float(direct_seconds),
            },
            "encoded_variant_extraction": timing_variants,
        },
        "notes": [
            "The same original source frame order is used for all variants; no temporal resampling is performed.",
            "Resize-only effect is measured by app_direct_native versus resized_640_png.",
            "JPEG-only effects after resizing are measured by resized_640_png versus the production-equivalent q92 JPEG variant.",
            "The q92 transform matches the current source app.js quality setting, with a 640-pixel canvas width; OpenCV resampling approximates browser canvas resampling. This branch is not actual browser Canvas. JPEG quality 72 is intentionally excluded from this current production-equivalent campaign.",
            "No checkpoint, label order, confidence threshold, 134-D representation, or 169-frame sequence contract is modified.",
            "This is a reference-video diagnostic. It does not establish live webcam recognition accuracy.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--resize-width", type=int, default=640, help="Current app canvas width (default 640)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.resize_width < 1:
        parser.error("--resize-width must be positive")
    video = args.video.expanduser().resolve()
    if not video.is_file():
        print(f"ERROR: video not found: {video}", file=sys.stderr)
        return 2
    try:
        report = diagnose(video, args.expected, args.resize_width)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    output = args.output if args.output.is_absolute() else Path.cwd() / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
