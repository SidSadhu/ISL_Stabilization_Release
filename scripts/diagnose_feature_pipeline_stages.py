#!/usr/bin/env python
"""Three-way INCLUDE feature-stage diagnostic: raw app, JPEG app, upstream generator.

This is a diagnostic, not a recognition accuracy evaluation. It compares:
  A. Application feature extractor on direct OpenCV-decoded BGR frames (no extra JPEG).
  B. The same application extractor after JPEG quality-90 encode/decode.
  C. Pinned AI4Bharat generate_keypoints.py on the original source video.

The upstream branch uses a current-MediaPipe first-25-points adapter where needed;
this is NOT exact historical upper_body_only detector parity.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import inspect
from pathlib import Path
from types import SimpleNamespace

import numpy as np


GROUPS = {
    "pose": (0, 50, 25),
    "hand1": (50, 92, 21),
    "hand2": (92, 134, 21),
}


def _interpolate_columns(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32).copy()
    if values.ndim != 2:
        raise ValueError(f"Expected 2-D time x feature matrix, received {values.shape}")
    x = np.arange(values.shape[0])
    for column in range(values.shape[1]):
        valid = np.isfinite(values[:, column])
        if valid.any():
            values[:, column] = np.interp(x, x[valid], values[valid, column])
        else:
            values[:, column] = 0.0
    return values


def feature_group_stats(left: np.ndarray, right: np.ndarray) -> dict:
    """Summarise element-wise absolute differences for equal-shaped arrays."""
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    if a.shape != b.shape:
        raise ValueError(f"Feature shapes differ: {a.shape} vs {b.shape}")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Feature arrays must be finite after interpolation")
    delta = np.abs(a - b)
    flat = delta.reshape(-1)
    return {
        "mean_absolute_difference": float(np.mean(flat)),
        "median_absolute_difference": float(np.median(flat)),
        "p95_absolute_difference": float(np.percentile(flat, 95)),
        "max_absolute_difference": float(np.max(flat)),
        "fraction_over_1": float(np.mean(flat > 1.0)),
        "fraction_over_10": float(np.mean(flat > 10.0)),
        "fraction_over_100": float(np.mean(flat > 100.0)),
        "element_count": int(flat.size),
    }


def feature_presence(raw_features: np.ndarray) -> dict[str, np.ndarray]:
    """Return pre-interpolation per-frame presence for pose and hand slots."""
    values = np.asarray(raw_features, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != 134:
        raise ValueError(f"Expected (frames,134), received {values.shape}")
    return {
        name: np.isfinite(values[:, start:end]).all(axis=1)
        for name, (start, end, _) in GROUPS.items()
    }


def presence_summary(raw_features: np.ndarray) -> dict:
    masks = feature_presence(raw_features)
    pose = masks["pose"]
    h1 = masks["hand1"]
    h2 = masks["hand2"]
    return {
        "pose": float(np.mean(pose)) if len(pose) else 0.0,
        "hand1": float(np.mean(h1)) if len(h1) else 0.0,
        "hand2": float(np.mean(h2)) if len(h2) else 0.0,
        "any_hand": float(np.mean(h1 | h2)) if len(h1) else 0.0,
        "both_hands": float(np.mean(h1 & h2)) if len(h1) else 0.0,
        "frames": int(len(pose)),
    }


def presence_disagreement(left: np.ndarray, right: np.ndarray) -> dict:
    lm, rm = feature_presence(left), feature_presence(right)
    if len(left) != len(right):
        raise ValueError(f"Presence frame counts differ: {len(left)} vs {len(right)}")
    result = {}
    for group in GROUPS:
        result[group] = {
            "mismatched_frames": int(np.count_nonzero(lm[group] != rm[group])),
            "frame_count": int(len(left)),
        }
    return result


def _top_dimensions(left: np.ndarray, right: np.ndarray, group: str, limit: int = 8) -> list[dict]:
    start, end, landmark_count = GROUPS[group]
    delta = np.abs(left[:, start:end] - right[:, start:end])
    mean_by_dim = delta.mean(axis=0)
    indices = np.argsort(mean_by_dim)[::-1][:limit]
    rows = []
    for index in indices:
        rows.append({
            "group_feature_index": int(index),
            "landmark_index": int(index // 2),
            "coordinate": "x" if index % 2 == 0 else "y",
            "mean_absolute_difference": float(mean_by_dim[index]),
        })
    return rows


def compare_feature_sequences(left: np.ndarray, right: np.ndarray) -> dict:
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    if a.shape != b.shape:
        raise ValueError(f"Feature sequence shapes differ: {a.shape} vs {b.shape}")
    result = {
        "overall": feature_group_stats(a, b),
        "groups": {},
        "top_frames_by_mean_difference": [],
    }
    frame_mean = np.abs(a - b).mean(axis=1)
    top_frames = np.argsort(frame_mean)[::-1][:min(10, len(frame_mean))]
    result["top_frames_by_mean_difference"] = [
        {"frame_index_zero_based": int(i), "mean_absolute_difference": float(frame_mean[i])}
        for i in top_frames
    ]
    for group, (start, end, _) in GROUPS.items():
        result["groups"][group] = feature_group_stats(a[:, start:end], b[:, start:end])
        result["groups"][group]["largest_dimensions"] = _top_dimensions(a, b, group)
    return result


def _official_arrays(row: dict) -> np.ndarray:
    pieces = []
    for prefix, count in (("pose", 25), ("hand1", 21), ("hand2", 21)):
        x = np.asarray(row[f"{prefix}_x"], dtype=np.float32)
        y = np.asarray(row[f"{prefix}_y"], dtype=np.float32)
        if x.ndim != 2 or y.shape != x.shape or x.shape[1] != count:
            raise ValueError(f"Unexpected {prefix} coordinate shapes: {x.shape} / {y.shape}")
        # Preserve NaNs to compute detection-presence masks before interpolation.
        xy = np.stack([x * 1920.0, y * 1080.0], axis=-1).reshape(x.shape[0], count * 2)
        pieces.append(xy)
    return np.concatenate(pieces, axis=1).astype(np.float32)


def _official_interpolated(row: dict) -> np.ndarray:
    raw = _official_arrays(row)
    return _interpolate_columns(raw)


def _install_pose_compatibility(mp) -> str:
    pose_cls = mp.solutions.pose.Pose
    try:
        params = inspect.signature(pose_cls).parameters
    except (TypeError, ValueError):
        params = {}
    if "upper_body_only" in params:
        return "native_legacy_upper_body_only_supported"

    class PoseCompat:
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
            return SimpleNamespace(pose_landmarks=SimpleNamespace(landmark=list(points[:25])))

        def close(self):
            return self._pose.close()

    mp.solutions.pose.Pose = PoseCompat
    return "compat_full_pose_first_25_points_not_exact_legacy_model"


def _app_direct_and_jpeg(video: Path, mp, cv2, jpeg_quality: int, mode: str = "verified_one_hand_fix"):
    """Read each source frame once; process direct pixels and retain only its JPEG bytes."""
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
    features = []
    direct_latencies_ms: list[float] = []
    jpeg_frames: list[bytes] = []
    pixel_mae: list[float] = []
    started = time.perf_counter()
    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
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
            direct_latencies_ms.append((time.perf_counter() - frame_started) * 1000.0)

            ok_enc, encoded = cv2.imencode(
                ".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
            )
            if not ok_enc:
                raise RuntimeError("JPEG encoding failed for a source frame")
            payload = encoded.tobytes()
            jpeg_frames.append(payload)
            decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            pixel_mae.append(float(np.mean(np.abs(bgr.astype(np.float32) - decoded.astype(np.float32)))))
    finally:
        cap.release()
        hands.close()
        pose.close()
    return (
        np.stack(features),
        jpeg_frames,
        pixel_mae,
        fps,
        declared_frames,
        direct_latencies_ms,
        time.perf_counter() - started,
    )


def _app_jpeg_extract(jpeg_frames, extractor):
    features = []
    latencies_ms = []
    session = extractor.start_session()
    started = time.perf_counter()
    try:
        for payload in jpeg_frames:
            frame_started = time.perf_counter()
            item = extractor.extract(payload, session)
            latencies_ms.append((time.perf_counter() - frame_started) * 1000.0)
            features.append(np.asarray(item.features, dtype=np.float32))
    finally:
        extractor.close_session(session)
    return np.stack(features), latencies_ms, time.perf_counter() - started


def _predict(model, padded: np.ndarray, labels: dict, torch, seed: int = 20261106) -> dict:
    from isl_recognition.labels import invert_label_map
    torch.manual_seed(seed)
    result = model.predict(padded)
    inv = invert_label_map(labels)
    indices = [int(x) for x in result["top5_indices"]]
    probs = [float(x) for x in result["top5_probabilities"]]
    return {
        "top1_label": inv[indices[0]],
        "top1_confidence": probs[0],
        "top5": [{"rank": i + 1, "label": inv[idx], "confidence": probs[i]} for i, idx in enumerate(indices)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--jpeg-quality", type=int, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be 1..100")
    video = args.video.expanduser().resolve()
    if not video.is_file():
        print(f"ERROR: video not found: {video}", file=sys.stderr)
        return 2

    try:
        import cv2
        import mediapipe as mp
        import torch
        from isl_recognition.app_config import Settings, resolve_label_map
        from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
        from isl_recognition.labels import load_label_map
        from isl_recognition.model import INCLUDETransformerSmall
        from isl_recognition.preprocess import preprocess_feature_sequence
    except Exception as exc:
        print(f"ERROR: project dependencies/imports: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    settings = Settings()
    settings.validate()
    repo = Path(settings.ai4bharat_repo).resolve()
    checkpoint = Path(settings.checkpoint).resolve()
    label_path = resolve_label_map(settings)
    if not repo.is_dir() or not checkpoint.is_file() or not label_path or not Path(label_path).is_file():
        print("ERROR: AI4BHARAT_REPO/checkpoint/label map could not be resolved.", file=sys.stderr)
        return 3
    labels = load_label_map(Path(label_path))
    expected = args.expected.strip().lower().replace(" ", "")
    if expected not in labels:
        print(f"ERROR: expected label {expected!r} not found in INCLUDE-50 label map.", file=sys.stderr)
        return 2

    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    try:
        from generate_keypoints import process_video as upstream_process_video
    except Exception as exc:
        print(f"ERROR: upstream generate_keypoints import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    pose_mode = _install_pose_compatibility(mp)

    # Process the original BGR frames directly through the app's landmark mapping,
    # while retaining only the JPEG payloads needed for the application evaluator path.
    direct_raw, jpeg_frames, pixel_mae, source_fps, declared_frames, direct_latencies_ms, direct_seconds = (
        _app_direct_and_jpeg(video, mp, cv2, args.jpeg_quality)
    )
    if len(direct_raw) == 0:
        print("ERROR: no source frames decoded", file=sys.stderr)
        return 4

    extractor = MediaPipeFeatureExtractor()
    jpeg_raw, jpeg_latencies_ms, jpeg_seconds = _app_jpeg_extract(jpeg_frames, extractor)
    jpeg_frames.clear()

    # Run the real upstream generator against the original source video.
    with tempfile.TemporaryDirectory(prefix="isl_stage_parity_") as temp:
        output_dir = Path(temp) / "keypoints"
        output_dir.mkdir()
        normalized = str(video).replace("\\", "/")
        upstream_process_video(normalized, str(output_dir))
        records = list(output_dir.glob("*.json"))
        if len(records) != 1:
            raise RuntimeError(f"Upstream generated {len(records)} JSON records; expected exactly one")
        row = json.loads(records[0].read_text(encoding="utf-8"))
        upstream_raw = _official_arrays(row)

    if not (len(direct_raw) == len(jpeg_raw) == len(upstream_raw)):
        raise RuntimeError(
            f"Frame count mismatch: app-direct={len(direct_raw)}, "
            f"app-jpeg={len(jpeg_raw)}, upstream={len(upstream_raw)}"
        )

    # Preserve pre-interpolation masks, then interpolate exactly as the evaluator does.
    direct_presence = presence_summary(direct_raw)
    jpeg_presence = presence_summary(jpeg_raw)
    upstream_presence = presence_summary(upstream_raw)

    direct_seq = _interpolate_columns(direct_raw)
    jpeg_seq = _interpolate_columns(jpeg_raw)
    upstream_seq = _interpolate_columns(upstream_raw)

    direct_padded = np.zeros((169, 134), dtype=np.float32)
    jpeg_padded = np.zeros((169, 134), dtype=np.float32)
    upstream_padded = np.zeros((169, 134), dtype=np.float32)
    for seq, padded in ((direct_seq, direct_padded), (jpeg_seq, jpeg_padded), (upstream_seq, upstream_padded)):
        if len(seq) > 169:
            raise ValueError(f"Video has {len(seq)} frames, above checkpoint length 169")
        padded[:len(seq)] = seq

    model = INCLUDETransformerSmall(repo, checkpoint)
    predictions = {
        "app_direct_no_extra_jpeg": _predict(model, direct_padded, labels, torch),
        "app_jpeg_quality_90": _predict(model, jpeg_padded, labels, torch),
        "upstream_current_mediapipe_adapter": _predict(model, upstream_padded, labels, torch),
    }

    comparisons = {
        "app_direct_vs_upstream": compare_feature_sequences(direct_seq, upstream_seq),
        "app_jpeg_vs_upstream": compare_feature_sequences(jpeg_seq, upstream_seq),
        "app_direct_vs_app_jpeg": compare_feature_sequences(direct_seq, jpeg_seq),
    }

    raw_presence_disagreement = {
        "app_direct_vs_upstream": presence_disagreement(direct_raw, upstream_raw),
        "app_jpeg_vs_upstream": presence_disagreement(jpeg_raw, upstream_raw),
        "app_direct_vs_app_jpeg": presence_disagreement(direct_raw, jpeg_raw),
    }

    report = {
        "diagnostic_type": "three_way_raw_vs_jpeg_app_vs_upstream_feature_stage_comparison",
        "warning": (
            "Upstream branch uses generate_keypoints.py with a first-25-landmark compatibility "
            "adapter under modern MediaPipe. This is not exact historical upper_body_only detector parity."
        ),
        "video": str(video),
        "expected_label": expected,
        "source_fps": source_fps,
        "declared_source_frames": declared_frames,
        "frames_processed_each_path": int(len(direct_seq)),
        "model_input_shape": [169, 134],
        "jpeg_quality": args.jpeg_quality,
        "upstream_pose_compatibility_mode": pose_mode,
        "pixel_roundtrip": {
            "mean_absolute_bgr_pixel_difference": float(np.mean(pixel_mae)),
            "p95_per_frame_bgr_pixel_difference": float(np.percentile(pixel_mae, 95)),
            "max_per_frame_bgr_pixel_difference": float(np.max(pixel_mae)),
        },
        "feature_presence_before_interpolation": {
            "app_direct_no_extra_jpeg": direct_presence,
            "app_jpeg_quality_90": jpeg_presence,
            "upstream_current_mediapipe_adapter": upstream_presence,
        },
        "hand_slot_presence_disagreement": raw_presence_disagreement,
        "feature_comparisons_after_interpolation": comparisons,
        "predictions_same_seed": predictions,
        "timings": {
            "app_direct_per_frame_ms": {
                "median": float(np.median(direct_latencies_ms)),
                "p95": float(np.percentile(direct_latencies_ms, 95)),
                "mean": float(np.mean(direct_latencies_ms)),
            },
            "app_jpeg_per_frame_ms_including_decode": {
                "median": float(np.median(jpeg_latencies_ms)),
                "p95": float(np.percentile(jpeg_latencies_ms, 95)),
                "mean": float(np.mean(jpeg_latencies_ms)),
            },
            "direct_branch_wall_seconds_including_video_decode_and_jpeg_encode": float(direct_seconds),
            "jpeg_branch_feature_loop_seconds": float(jpeg_seconds),
        },
        "notes": [
            "App-direct and app-JPEG branches use the application's landmark ordering and feature construction.",
            "App-JPEG uses the same JPEG-90 extraction route as the completed official validation evaluator.",
            "The direct branch measures MediaPipe on original OpenCV-decoded BGR frames; the JPEG branch measures the application's JPEG decode plus MediaPipe path.",
            "Per-frame extractor timings are reported separately from the full branch wall times; the latter include different setup/decode/encode work and should not be compared directly.",
            "Feature comparisons exclude padded rows and use the same interpolated/scaled 134-D sequence representation.",
            "No model weights, label order, confidence threshold, or project preprocessing are modified.",
        ],
    }

    output = args.output if args.output.is_absolute() else Path.cwd() / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
