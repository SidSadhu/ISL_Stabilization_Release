#!/usr/bin/env python3
"""Phase 3: hand detector stability and feature-slot assignment diagnostics.

Read-only/offline diagnostic. It compares the exact saved browser Canvas JPEG
frames against timestamp-matched frames decoded from the original INCLUDE videos.
It records MediaPipe hand count, handedness labels/scores, detector order, pose-wrist
proximity, production slot mapping, a wrist-nearest counterfactual, and interpolation
impact. It does not load or modify the classifier/checkpoint and never emits IoT.

Handedness labels are MediaPipe image-side classifications. Because capture input
is intentionally NOT mirrored, they must not be interpreted as guaranteed physical
left/right identities without a separate mirror-aware calibration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import parse_capture_payload  # noqa: E402
from prepare_official_validation_manifest import (  # noqa: E402
    EXPECTED_VAL_COUNT,
    OFFICIAL_VAL_LIST_SHA256,
    PINNED_REPO_COMMIT,
    load_official_validation_rows,
)
from video_path_utils import resolve_manifest_video  # noqa: E402

EXPECTED_CHECKPOINT_SHA256 = "8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4"
TARGETS = {
    "Greetings/51. Good Morning/MVI_0099.MOV": "transport/hand-slot outlier; expected goodmorning",
    "Colours/55. White/MVI_5063.MOV": "hand-slot/presence outlier; expected white",
    "Adjectives/83. big large/MVI_9396.MOV": "large coordinate-drift outlier; expected biglarge",
    "Adjectives/79. short/MVI_9302.MOV": "recovered missing-pose case; expected short",
    "Means_of_Transportation/11. Car/MVI_3118.MOV": "stable recognition control; expected car",
    "Pronouns/40. I/MVI_9922.MOV": "stable recognition control; expected i",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_path(value: str) -> str:
    return str(value).replace("\\", "/").strip().casefold()


def _finite_xy(point: Any) -> tuple[float, float] | None:
    try:
        x, y = float(point.x), float(point.y)
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _nearest_pose_wrist(hand_xy: tuple[float, float], left_xy: tuple[float, float] | None,
                        right_xy: tuple[float, float] | None, width: int, height: int) -> dict[str, Any]:
    result: dict[str, Any] = {"nearest_pose_wrist": None, "left_wrist_distance_px": None,
                              "right_wrist_distance_px": None}
    if left_xy is not None:
        result["left_wrist_distance_px"] = math.hypot(
            (hand_xy[0] - left_xy[0]) * width, (hand_xy[1] - left_xy[1]) * height
        )
    if right_xy is not None:
        result["right_wrist_distance_px"] = math.hypot(
            (hand_xy[0] - right_xy[0]) * width, (hand_xy[1] - right_xy[1]) * height
        )
    distances = {"pose_left_wrist": result["left_wrist_distance_px"],
                 "pose_right_wrist": result["right_wrist_distance_px"]}
    valid = {k: v for k, v in distances.items() if v is not None}
    if valid:
        result["nearest_pose_wrist"] = min(valid, key=valid.get)
    return result


def detector_to_slot_map(raw_hands: list[Any], mapped_frame: Any) -> dict[str, int | None]:
    """Identify detector index occupying each mapped feature slot using wrist XY."""
    mapping: dict[str, int | None] = {"hand1": None, "hand2": None}
    for slot, xs, ys in (
        ("hand1", mapped_frame.hand1_x, mapped_frame.hand1_y),
        ("hand2", mapped_frame.hand2_x, mapped_frame.hand2_y),
    ):
        if not xs or not ys or not math.isfinite(float(xs[0])) or not math.isfinite(float(ys[0])):
            continue
        target = (float(xs[0]), float(ys[0]))
        distances = []
        for index, hand in enumerate(raw_hands):
            point = _finite_xy(hand.landmark[0])
            if point is None:
                continue
            distances.append((math.hypot(point[0] - target[0], point[1] - target[1]), index))
        if distances:
            distance, index = min(distances)
            if distance <= 1e-5:
                mapping[slot] = int(index)
    return mapping


def summarize_interpolation(features: Any) -> dict[str, Any]:
    """Measure missing raw features and the number of values filled by interpolation."""
    import numpy as np
    from isl_recognition.preprocess import interpolate_scaled_sequence

    matrix = np.asarray(features, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != 134:
        raise ValueError(f"Expected feature matrix (frames,134), got {matrix.shape}")
    missing = ~np.isfinite(matrix)
    interpolated = interpolate_scaled_sequence(matrix)
    per_frame_missing = missing.sum(axis=1)
    group_ranges = {"pose": (0, 50), "hand1": (50, 92), "hand2": (92, 134)}
    groups = {}
    for group, (start, end) in group_ranges.items():
        group_missing = missing[:, start:end]
        frame_mask = group_missing.any(axis=1)
        longest_run = run = 0
        for is_missing in frame_mask:
            run = run + 1 if is_missing else 0
            longest_run = max(longest_run, run)
        groups[group] = {
            "missing_coordinate_count": int(group_missing.sum()),
            "frames_with_any_missing_coordinate": int(frame_mask.sum()),
            "frame_count": int(len(matrix)),
            "missing_coordinate_fraction": float(group_missing.mean()) if group_missing.size else 0.0,
            "longest_consecutive_frames_with_missing_group": int(longest_run),
        }
    return {
        "frame_count": int(len(matrix)),
        "missing_coordinate_count_before_interpolation": int(missing.sum()),
        "frames_with_any_missing_coordinate": int((per_frame_missing > 0).sum()),
        "frames_with_all_features_finite_after_interpolation": int(np.isfinite(interpolated).all(axis=1).sum()),
        "imputed_coordinate_count": int(missing.sum()),
        "groups": groups,
    }


def load_capture_map(capture_dir: Path, expected_by_path: dict[str, dict[str, str]]) -> dict[str, tuple[Path, Any]]:
    clips_dir = capture_dir / "clips"
    if not clips_dir.is_dir():
        raise RuntimeError(f"Browser capture folder missing: {clips_dir}")
    selected: dict[str, tuple[Path, Any]] = {}
    for file in sorted(clips_dir.glob("*.islbrw")):
        payload = file.read_bytes()
        if len(payload) < 12 or payload[:8] != b"ISLBRW01":
            continue
        header_len = int.from_bytes(payload[8:12], "big")
        try:
            header = json.loads(payload[12:12 + header_len].decode("utf-8"))
            path_key = norm_path(header["case"]["video_path"])
        except Exception:
            continue
        if path_key not in expected_by_path:
            continue
        if path_key in selected:
            raise RuntimeError(f"Duplicate browser capture found for {header['case']['video_path']}")
        parsed = parse_capture_payload(payload, expected_by_path[path_key])
        selected[path_key] = (file, parsed)
    return selected


def _process_variant(variant: str, frames: list[Any], timestamps: list[float], source_meta: dict[str, Any] | None,
                     mp: Any, cv2: Any, output_rows: list[dict[str, Any]], identity: dict[str, str]) -> dict[str, Any]:
    """Process sequential frames with fresh detector sessions and preserve raw hand metadata."""
    import numpy as np
    from isl_recognition.landmarks import landmark_results_to_feature_frame
    from isl_recognition.preprocess import make_scaled_frame_feature

    hands = mp.solutions.hands.Hands(
        static_image_mode=False, max_num_hands=2,
        min_detection_confidence=0.5, min_tracking_confidence=0.5,
    )
    pose = mp.solutions.pose.Pose(
        static_image_mode=False, min_detection_confidence=0.5,
        min_tracking_confidence=0.5, model_complexity=1,
    )
    feature_rows: list[np.ndarray] = []
    frame_rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    try:
        for frame_i, frame_source in enumerate(frames):
            if variant == "browser_canvas":
                image_bgr = cv2.imdecode(np.frombuffer(frame_source, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image_bgr is None:
                    raise RuntimeError(f"Cannot decode browser JPEG at frame {frame_i}")
            else:
                image_bgr = frame_source
            height, width = image_bgr.shape[:2]
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            hand_result = hands.process(rgb)
            pose_result = pose.process(rgb)
            raw_hands = list(getattr(hand_result, "multi_hand_landmarks", None) or [])
            handedness = list(getattr(hand_result, "multi_handedness", None) or [])
            pose_points = getattr(getattr(pose_result, "pose_landmarks", None), "landmark", None)
            pose_found = bool(pose_points is not None and len(pose_points) >= 25)
            left_wrist = _finite_xy(pose_points[15]) if pose_found else None
            right_wrist = _finite_xy(pose_points[16]) if pose_found else None

            mapped_default = landmark_results_to_feature_frame(hand_result, pose_result, "verified_one_hand_fix")
            mapped_nearest = landmark_results_to_feature_frame(hand_result, pose_result, "wrist_nearest")
            default_slot_map = detector_to_slot_map(raw_hands, mapped_default)
            nearest_slot_map = detector_to_slot_map(raw_hands, mapped_nearest)
            feature_vec = make_scaled_frame_feature(
                mapped_default.pose_x, mapped_default.pose_y,
                mapped_default.hand1_x, mapped_default.hand1_y,
                mapped_default.hand2_x, mapped_default.hand2_y,
                allow_nan=True,
            ).astype(np.float32)
            feature_rows.append(feature_vec)

            labels: list[str] = []
            scores: list[float | None] = []
            for handed in handedness:
                classifications = getattr(handed, "classification", None) or []
                if classifications:
                    labels.append(str(getattr(classifications[0], "label", "Unknown")))
                    score = getattr(classifications[0], "score", None)
                    scores.append(float(score) if score is not None and math.isfinite(float(score)) else None)
                else:
                    labels.append("Unknown")
                    scores.append(None)

            frame_record = {
                **identity,
                "variant": variant,
                "frame_index": frame_i,
                "browser_video_time_seconds": float(timestamps[frame_i]),
                "source_frame_index": source_meta["frame_indices"][frame_i] if source_meta else None,
                "source_frame_time_seconds": source_meta["frame_times"][frame_i] if source_meta else None,
                "timestamp_alignment_delta_ms": source_meta["alignment_delta_ms"][frame_i] if source_meta else None,
                "source_terminal_frame_hold": source_meta["terminal_frame_hold_flags"][frame_i] if source_meta else False,
                "frame_width": int(width), "frame_height": int(height),
                "pose_found": bool(pose_found),
                "pose_left_wrist_x_norm": left_wrist[0] if left_wrist else None,
                "pose_left_wrist_y_norm": left_wrist[1] if left_wrist else None,
                "pose_right_wrist_x_norm": right_wrist[0] if right_wrist else None,
                "pose_right_wrist_y_norm": right_wrist[1] if right_wrist else None,
                "detected_hand_count": len(raw_hands),
                "handedness_labels_in_detector_order": "|".join(labels),
                "handedness_scores_in_detector_order": "|".join("" if s is None else f"{s:.6f}" for s in scores),
                "production_hand1_detector_index": default_slot_map["hand1"],
                "production_hand2_detector_index": default_slot_map["hand2"],
                "wrist_nearest_hand1_detector_index": nearest_slot_map["hand1"],
                "wrist_nearest_hand2_detector_index": nearest_slot_map["hand2"],
                "slot_assignment_changed_by_wrist_nearest": (
                    default_slot_map != nearest_slot_map
                ),
                "hand1_feature_present_before_interpolation": bool(np.isfinite(feature_vec[50:92]).all()),
                "hand2_feature_present_before_interpolation": bool(np.isfinite(feature_vec[92:134]).all()),
                "missing_feature_coordinates_before_interpolation": int((~np.isfinite(feature_vec)).sum()),
            }
            for det_i, hand in enumerate(raw_hands):
                wrist = _finite_xy(hand.landmark[0]) if len(hand.landmark) else None
                proximity = _nearest_pose_wrist(wrist, left_wrist, right_wrist, width, height) if wrist else {}
                frame_record[f"det{det_i + 1}_wrist_x_norm"] = wrist[0] if wrist else None
                frame_record[f"det{det_i + 1}_wrist_y_norm"] = wrist[1] if wrist else None
                frame_record[f"det{det_i + 1}_nearest_pose_wrist"] = proximity.get("nearest_pose_wrist")
                frame_record[f"det{det_i + 1}_pose_left_wrist_distance_px"] = proximity.get("left_wrist_distance_px")
                frame_record[f"det{det_i + 1}_pose_right_wrist_distance_px"] = proximity.get("right_wrist_distance_px")
                for landmark_i, point in enumerate(hand.landmark):
                    frame_record[f"det{det_i + 1}_lm{landmark_i:02d}_x_norm"] = float(point.x)
                    frame_record[f"det{det_i + 1}_lm{landmark_i:02d}_y_norm"] = float(point.y)
            for slot, mapping in (("production", default_slot_map), ("wrist_nearest", nearest_slot_map)):
                for slot_name, det_i in mapping.items():
                    frame_record[f"{slot}_map_{slot_name}"] = det_i
            frame_rows.append(frame_record)
            output_rows.append(frame_record)
    finally:
        hands.close()
        pose.close()

    return {
        "variant": variant,
        "frame_count": len(frames),
        "duration_seconds": time.perf_counter() - started,
        "interpolation": summarize_interpolation(np.stack(feature_rows)),
        "frames": frame_rows,
        "features": np.stack(feature_rows),
    }


def _nearest_source_frame_target(timestamp: float, fps: float, frame_count: int) -> tuple[int, bool]:
    """Return nearest valid source index and whether a terminal hold is needed."""
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError(f"Invalid source timestamp: {timestamp!r}")
    if not math.isfinite(fps) or fps <= 0 or frame_count < 1:
        raise ValueError(f"Invalid source video metadata: fps={fps!r}, frame_count={frame_count!r}")
    requested_index = max(0, int(math.floor(timestamp * fps + 0.5)))
    if requested_index < frame_count:
        return requested_index, False
    media_duration = frame_count / fps
    terminal_limit = media_duration + (0.5 / fps) + 1e-6
    if timestamp <= terminal_limit:
        return frame_count - 1, True
    raise RuntimeError(
        "Native source timestamp exceeds the decodable video interval: "
        f"timestamp={timestamp:.6f}s; requested_frame={requested_index}; "
        f"fps={fps:.6f}; declared_frames={frame_count}; duration={media_duration:.6f}s; "
        f"terminal_limit={terminal_limit:.6f}s"
    )


def _timestamp_matched_source(video_path: Path, timestamps: list[float], cv2: Any) -> tuple[list[Any], dict[str, Any]]:
    """Decode nearest source frames, allowing only a bounded final-frame hold.

    HTML video playback can report currentTime at the container duration while
    displaying its final decoded frame. Rounding that terminal timestamp can
    request frame_count, although valid frame indices are 0..frame_count-1.
    That one terminal case is now represented explicitly; premature decode
    failures still fail closed.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open source video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if not math.isfinite(fps) or fps <= 0:
        cap.release()
        raise RuntimeError(f"Source FPS invalid for {video_path}: fps={fps!r}, frames={frame_count}")
    if frame_count < 1:
        cap.release()
        raise RuntimeError(f"Source frame count unavailable for {video_path}: fps={fps:.6f}, frames={frame_count}")

    frames: list[Any] = []
    indices: list[int] = []
    times: list[float] = []
    deltas: list[float] = []
    terminal_holds: list[bool] = []
    next_index = 0
    cached_frame = None
    cached_index = -1
    try:
        for timestamp_value in timestamps:
            timestamp = float(timestamp_value)
            try:
                target_index, terminal_hold = _nearest_source_frame_target(timestamp, fps, frame_count)
            except RuntimeError as exc:
                raise RuntimeError(f"{exc}; video={video_path}") from exc

            while next_index <= target_index:
                ok, decoded = cap.read()
                if not ok or decoded is None:
                    break
                cached_frame = decoded
                cached_index = next_index
                next_index += 1

            if cached_frame is None or cached_index != target_index:
                # At an allowed terminal timestamp the source's final frame is
                # the correct displayed image. Any earlier gap/corruption remains
                # a hard error; no arbitrary previous frame is substituted.
                if not (terminal_hold and cached_frame is not None and cached_index == frame_count - 1):
                    raise RuntimeError(
                        "Could not decode native source frame "
                        f"{target_index} at {timestamp:.6f}s; video={video_path}; "
                        f"fps={fps:.6f}; declared_frames={frame_count}; "
                        f"last_decoded_frame={cached_index}; terminal_hold={terminal_hold}"
                    )

            frames.append(cached_frame.copy())
            indices.append(target_index)
            native_time = target_index / fps
            times.append(native_time)
            deltas.append((native_time - timestamp) * 1000.0)
            terminal_holds.append(terminal_hold)
    finally:
        cap.release()

    max_abs_delta = max((abs(x) for x in deltas), default=0.0)
    normal_deltas = [abs(delta) for delta, terminal in zip(deltas, terminal_holds) if not terminal]
    terminal_deltas = [abs(delta) for delta, terminal in zip(deltas, terminal_holds) if terminal]
    normal_limit_ms = 500.0 / fps + 0.5
    terminal_limit_ms = 1500.0 / fps + 0.5
    if max(normal_deltas, default=0.0) > normal_limit_ms:
        raise RuntimeError(
            f"Timestamp-to-source alignment exceeds half-frame tolerance for {video_path}: "
            f"max_nonterminal_error_ms={max(normal_deltas):.3f}, limit_ms={normal_limit_ms:.3f}"
        )
    if max(terminal_deltas, default=0.0) > terminal_limit_ms:
        raise RuntimeError(
            f"Terminal-frame alignment exceeds bounded end-of-stream tolerance for {video_path}: "
            f"max_terminal_error_ms={max(terminal_deltas):.3f}, limit_ms={terminal_limit_ms:.3f}"
        )

    meta = {
        "fps": fps, "declared_frame_count": frame_count,
        "declared_duration_seconds": frame_count / fps,
        "frame_indices": indices, "frame_times": times,
        "alignment_delta_ms": deltas,
        "terminal_frame_hold_flags": terminal_holds,
        "terminal_frame_hold_count": int(sum(terminal_holds)),
        "terminal_frame_hold_browser_timestamps_seconds": [
            float(ts) for ts, held in zip(timestamps, terminal_holds) if held
        ],
        "max_abs_alignment_delta_ms": max_abs_delta,
        "max_nonterminal_alignment_delta_ms": max(normal_deltas, default=0.0),
        "max_terminal_alignment_delta_ms": max(terminal_deltas, default=0.0),
        "source_width": int(frames[0].shape[1]) if frames else 0,
        "source_height": int(frames[0].shape[0]) if frames else 0,
    }
    return frames, meta


def compare_handedness(browser_rows: list[dict[str, Any]], native_rows: list[dict[str, Any]]) -> dict[str, Any]:
    paired = min(len(browser_rows), len(native_rows))
    exact_count_match = sum(browser_rows[i]["detected_hand_count"] == native_rows[i]["detected_hand_count"] for i in range(paired))
    both_have_two = sum(browser_rows[i]["detected_hand_count"] == 2 and native_rows[i]["detected_hand_count"] == 2 for i in range(paired))
    detector_label_match = 0
    label_comparable = 0
    for i in range(paired):
        a = browser_rows[i]["handedness_labels_in_detector_order"].split("|") if browser_rows[i]["handedness_labels_in_detector_order"] else []
        b = native_rows[i]["handedness_labels_in_detector_order"].split("|") if native_rows[i]["handedness_labels_in_detector_order"] else []
        if a and b and len(a) == len(b):
            label_comparable += 1
            if a == b:
                detector_label_match += 1
    return {
        "paired_frame_count": paired,
        "same_detected_hand_count_frames": exact_count_match,
        "same_detected_hand_count_fraction": exact_count_match / paired if paired else None,
        "both_variants_detect_two_hands_frames": both_have_two,
        "handedness_label_comparable_frames": label_comparable,
        "same_ordered_handedness_label_frames": detector_label_match,
        "same_ordered_handedness_label_fraction_when_comparable": detector_label_match / label_comparable if label_comparable else None,
        "browser_two_hand_frame_count": sum(r["detected_hand_count"] == 2 for r in browser_rows),
        "native_two_hand_frame_count": sum(r["detected_hand_count"] == 2 for r in native_rows),
        "browser_any_hand_frame_count": sum(r["detected_hand_count"] > 0 for r in browser_rows),
        "native_any_hand_frame_count": sum(r["detected_hand_count"] > 0 for r in native_rows),
        "browser_production_slot_swaps_vs_wrist_nearest": sum(bool(r["slot_assignment_changed_by_wrist_nearest"]) for r in browser_rows),
        "native_production_slot_swaps_vs_wrist_nearest": sum(bool(r["slot_assignment_changed_by_wrist_nearest"]) for r in native_rows),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    import cv2
    import mediapipe as mp
    import numpy as np
    from isl_recognition.app_config import Settings
    from isl_recognition.labels import load_label_map
    from isl_recognition.iot_protocol import load_label_commands

    repo, data_root = args.repo.resolve(), args.data_root.resolve()
    manifest_path, capture_dir = args.manifest.resolve(), args.capture_dir.resolve()
    for path, name in ((repo, "AI4Bharat repository"), (data_root, "source video root"),
                       (manifest_path, "official manifest"), (capture_dir, "browser capture directory")):
        if not path.exists():
            raise RuntimeError(f"Missing required {name}: {path}")
    commit_rows, commit, split_hash = load_official_validation_rows(
        repo, set(load_label_map(ROOT / "config" / "label_map_include50.json"))
    )
    if commit != PINNED_REPO_COMMIT or split_hash != OFFICIAL_VAL_LIST_SHA256 or len(commit_rows) != EXPECTED_VAL_COUNT:
        raise RuntimeError("Pinned official INCLUDE-50 validation split verification failed")
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        manifest_rows = list(csv.DictReader(stream))
    official_pairs = {(r["video_path"].casefold(), r["label"]) for r in commit_rows}
    supplied_pairs = {(norm_path(r.get("video_path", "")), str(r.get("label", "")).strip()) for r in manifest_rows}
    if len(manifest_rows) != 77 or supplied_pairs != official_pairs:
        raise RuntimeError("Manifest must be exactly the pinned official 77-item validation manifest")
    if any(r.get("split", "val") != "val" for r in manifest_rows):
        raise RuntimeError("Manifest includes a non-validation row; blocked")

    settings = Settings()
    settings.validate()
    if settings.pre_iot_lockdown is not True or settings.iot_default_enabled:
        raise RuntimeError("IoT hard-lock must be on and ISL_IOT_ENABLED must be false")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("Process environment ISL_IOT_ENABLED is true; diagnostics blocked")
    label_map = load_label_map(ROOT / "config" / "label_map_include50.json")
    mappings = load_label_commands(settings.iot_label_map_path, label_map)
    if mappings:
        raise RuntimeError("IoT mapping must be empty for Phase 3")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 3 requires Python 3.12; found {sys.version.split()[0]}")
    if mp.__version__ != "0.10.21" or cv2.__version__ != "4.11.0":
        raise RuntimeError(f"Expected MediaPipe 0.10.21/OpenCV 4.11.0; found {mp.__version__}/{cv2.__version__}")

    expected_by_path = {norm_path(row["video_path"]): row for row in commit_rows}
    captured = load_capture_map(capture_dir, expected_by_path)
    manifest_by_path = {norm_path(row["video_path"]): row for row in commit_rows}
    outdir = args.output_dir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    frame_records: list[dict[str, Any]] = []
    clip_summaries: list[dict[str, Any]] = []
    coordinate_records: list[dict[str, Any]] = []
    started_all = time.perf_counter()

    for relative, purpose in TARGETS.items():
        key = norm_path(relative)
        if key not in captured:
            raise RuntimeError(f"Required browser Canvas capture missing: {relative}")
        manifest_row = manifest_by_path.get(key)
        if not manifest_row:
            raise RuntimeError(f"Target clip absent from official validation manifest: {relative}")
        capture_file, parsed = captured[key]
        video_path = resolve_manifest_video(data_root, relative)
        if not video_path.is_file():
            raise RuntimeError(f"Original source video missing: {video_path}")
        times = [float(frame["video_time_seconds"]) for frame in parsed.header["case"]["frames"]]
        native_frames, source_meta = _timestamp_matched_source(video_path, times, cv2)
        identity = {
            "video_path": relative, "expected_label": manifest_row["label"],
            "diagnostic_role": purpose, "capture_file": capture_file.name,
            "capture_sha256": parsed.sha256,
        }
        browser = _process_variant("browser_canvas", list(parsed.frames), times, source_meta, mp, cv2, frame_records, identity)
        native = _process_variant("native_timestamp_matched", native_frames, times, source_meta, mp, cv2, frame_records, identity)
        paired = compare_handedness(browser["frames"], native["frames"])

        # Coordinate comparison is made by unique detector handedness labels, not by
        # hand1/hand2 slots. MediaPipe labels are treated as image-side labels only.
        coordinate_pairs = []
        for i, (brow, nat) in enumerate(zip(browser["frames"], native["frames"])):
            bl = brow["handedness_labels_in_detector_order"].split("|") if brow["handedness_labels_in_detector_order"] else []
            nl = nat["handedness_labels_in_detector_order"].split("|") if nat["handedness_labels_in_detector_order"] else []
            b_label_to_det = {label: j for j, label in enumerate(bl) if label in ("Left", "Right") and bl.count(label) == 1}
            n_label_to_det = {label: j for j, label in enumerate(nl) if label in ("Left", "Right") and nl.count(label) == 1}
            common = sorted(set(b_label_to_det) & set(n_label_to_det))
            if not common:
                continue
            b_frame_idx = i
            for label in common:
                bd, nd = b_label_to_det[label], n_label_to_det[label]
                b_row, n_row = browser["frames"][b_frame_idx], native["frames"][b_frame_idx]
                # Wrist positions only are kept in the frame CSV. The full feature
                # coordinates are compared in slot-agnostic aggregate below by labels
                # using a second direct extraction of point data is intentionally not
                # fabricated from assigned slots. Detailed raw point traces are emitted
                # by optional per-hand landmark CSV columns in the per-frame output.
                bx, by = b_row.get(f"det{bd + 1}_wrist_x_norm"), b_row.get(f"det{bd + 1}_wrist_y_norm")
                nx, ny = n_row.get(f"det{nd + 1}_wrist_x_norm"), n_row.get(f"det{nd + 1}_wrist_y_norm")
                if None not in (bx, by, nx, ny):
                    point_differences = []
                    for landmark_i in range(21):
                        b_x = brow.get(f"det{bd + 1}_lm{landmark_i:02d}_x_norm")
                        b_y = brow.get(f"det{bd + 1}_lm{landmark_i:02d}_y_norm")
                        n_x = nat.get(f"det{nd + 1}_lm{landmark_i:02d}_x_norm")
                        n_y = nat.get(f"det{nd + 1}_lm{landmark_i:02d}_y_norm")
                        if None not in (b_x, b_y, n_x, n_y):
                            point_differences.extend([abs((b_x - n_x) * 1920.0), abs((b_y - n_y) * 1080.0)])
                    coordinate_row = {**identity,
                                      "frame_index": i, "browser_video_time_seconds": times[i],
                                      "handedness_image_label": label,
                                      "browser_detector_index": bd, "native_detector_index": nd,
                                      "browser_wrist_x_norm": bx, "browser_wrist_y_norm": by,
                                      "native_wrist_x_norm": nx, "native_wrist_y_norm": ny,
                                      "wrist_normalized_distance": math.hypot(bx - nx, by - ny),
                                      "matched_landmark_coordinate_count": len(point_differences),
                                      "hand_21pt_coordinate_mae_scaled": statistics.mean(point_differences) if point_differences else None,
                                      "hand_21pt_coordinate_p95_scaled": float(np.percentile(point_differences, 95)) if point_differences else None}
                    coordinate_pairs.append(coordinate_row)
                    coordinate_records.append(coordinate_row)
        wrist_distances = [r["wrist_normalized_distance"] for r in coordinate_pairs]
        hand_coordinate_maes = [r["hand_21pt_coordinate_mae_scaled"] for r in coordinate_pairs if r["hand_21pt_coordinate_mae_scaled"] is not None]
        slot_changed = paired["browser_production_slot_swaps_vs_wrist_nearest"] + paired["native_production_slot_swaps_vs_wrist_nearest"]
        clip_summaries.append({
            "video_path": relative, "expected_label": manifest_row["label"],
            "diagnostic_role": purpose, "browser_capture_sha256": parsed.sha256,
            "browser_frame_count": len(parsed.frames), "native_source_fps": source_meta["fps"],
            "native_source_frame_count": source_meta["declared_frame_count"],
            "timestamp_alignment_max_abs_error_ms": source_meta["max_abs_alignment_delta_ms"],
            "terminal_frame_hold_count": source_meta["terminal_frame_hold_count"],
            "terminal_frame_hold_browser_timestamps_seconds": source_meta["terminal_frame_hold_browser_timestamps_seconds"],
            "browser_detection_presence": {
                "pose_frames": sum(bool(r["pose_found"]) for r in browser["frames"]),
                "any_hand_frames": sum(r["detected_hand_count"] > 0 for r in browser["frames"]),
                "two_hand_frames": sum(r["detected_hand_count"] == 2 for r in browser["frames"]),
            },
            "native_detection_presence": {
                "pose_frames": sum(bool(r["pose_found"]) for r in native["frames"]),
                "any_hand_frames": sum(r["detected_hand_count"] > 0 for r in native["frames"]),
                "two_hand_frames": sum(r["detected_hand_count"] == 2 for r in native["frames"]),
            },
            "paired_detector_comparison": paired,
            "wrist_coordinate_pairs_by_unique_handedness_label": len(wrist_distances),
            "wrist_normalized_distance_mean": statistics.mean(wrist_distances) if wrist_distances else None,
            "wrist_normalized_distance_p95": float(np.percentile(wrist_distances, 95)) if wrist_distances else None,
            "unique_handedness_matched_hand_pairs": len(hand_coordinate_maes),
            "hand_21pt_coordinate_mae_scaled_mean": statistics.mean(hand_coordinate_maes) if hand_coordinate_maes else None,
            "hand_21pt_coordinate_mae_scaled_p95": float(np.percentile(hand_coordinate_maes, 95)) if hand_coordinate_maes else None,
            "browser_plus_native_frames_with_slot_counterfactual_change": slot_changed,
            "browser_interpolation": browser["interpolation"],
            "native_interpolation": native["interpolation"],
            "browser_processing_seconds": browser["duration_seconds"],
            "native_processing_seconds": native["duration_seconds"],
        })

    runtime = {
        "python": sys.version.split()[0], "platform": sys.platform,
        "opencv": cv2.__version__, "mediapipe": mp.__version__,
        "iot_enabled": False, "iot_mapping_count": 0, "pre_iot_lockdown": True,
    }
    result = {
        "campaign": "phase3_hand_assignment_detector_stability",
        "status": "completed",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selected_clip_count": len(TARGETS), "successful_clip_count": len(clip_summaries),
        "failed_clip_count": 0, "official_validation_split_sha256": split_hash,
        "official_repo_commit": commit,
        "checkpoint_sha256_reference_only_not_loaded": EXPECTED_CHECKPOINT_SHA256,
        "methodology": {
            "browser_frames": "exact JPEG byte payloads from the saved ISLBRW01 Canvas capture containers",
            "native_frames": "OpenCV source frames sampled at nearest frame index floor(video_time_seconds * source_fps + 0.5); a request rounded to/past declared frame_count is mapped to the final decoded frame only within half a frame of declared container duration",
            "time_alignment": "nearest source-frame alignment within half a frame; bounded final-frame hold may differ by up to 1.5 frame intervals and is explicitly flagged per frame/clip",
            "handedness": "MediaPipe image-side labels/scores; not asserted as physical left/right identity",
            "production_assignment": "verified_one_hand_fix: one-hand pose-wrist placement; two-hand detector order retained",
            "counterfactual_assignment": "wrist_nearest; diagnostic only, never applied to production",
            "interpolation": "measured after feature extraction using the existing 134-D per-coordinate temporal interpolation function",
            "classifier": "not loaded; no predictions, training, threshold tuning, or checkpoint changes",
            "iot": "disabled; no MQTT/network/action code invoked",
        },
        "runtime": runtime,
        "elapsed_seconds": time.perf_counter() - started_all,
        "clips": clip_summaries,
        "files": {},
    }
    write_csv(outdir / "phase3_per_frame_hand_diagnostics.csv", frame_records)
    write_csv(outdir / "phase3_per_hand_coordinate_comparison.csv", coordinate_records)
    write_csv(outdir / "phase3_clip_summary.csv", [
        {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()}
        for row in clip_summaries
    ])
    (outdir / "phase3_summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    report = build_report(result)
    (outdir / "PHASE3_HAND_ASSIGNMENT_REPORT.md").write_text(report, encoding="utf-8")
    result["files"] = {
        name: {"sha256": sha256_file(outdir / name), "bytes": (outdir / name).stat().st_size}
        for name in ("phase3_per_frame_hand_diagnostics.csv", "phase3_per_hand_coordinate_comparison.csv", "phase3_clip_summary.csv", "PHASE3_HAND_ASSIGNMENT_REPORT.md")
    }
    (outdir / "phase3_summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def build_report(result: dict[str, Any]) -> str:
    lines = [
        "# Phase 3 — Hand Assignment & Detector Stability", "",
        f"**Status:** {result['status']}  ",
        f"**Clips:** {result['successful_clip_count']}/{result['selected_clip_count']}  ",
        f"**Official validation path-list SHA256:** `{result['official_validation_split_sha256']}`", "",
        "## Purpose", "",
        "This diagnostic separates detector presence, handedness classification, feature-slot assignment, and temporal interpolation. It compares exact saved browser Canvas JPEG frames with timestamp-matched original-video frames. It does not evaluate classifier accuracy or alter production behavior.", "",
        "## Per-clip findings", "",
        "| Clip | Role | Browser hands (any / two) | Native hands (any / two) | Same count | Ordered label match (comparable only) | Slot changes (browser + native streams) | Wrist distance mean |", "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["clips"]:
        b, n, p = row["browser_detection_presence"], row["native_detection_presence"], row["paired_detector_comparison"]
        wrist = row["wrist_normalized_distance_mean"]
        wrist_text = f"{wrist:.5f}" if wrist is not None else "unavailable"
        lines.append(
            f"| `{Path(row['video_path']).name}` ({row['expected_label']}) | {row['diagnostic_role'].split(';')[0]} | "
            f"{b['any_hand_frames']}/{b['two_hand_frames']} | {n['any_hand_frames']}/{n['two_hand_frames']} | "
            f"{p['same_detected_hand_count_frames']}/{p['paired_frame_count']} | "
            f"{p['same_ordered_handedness_label_frames']}/{p['handedness_label_comparable_frames']} | "
            f"{row['browser_plus_native_frames_with_slot_counterfactual_change']} | {wrist_text} |")
    lines += [
        "", "## Interpretation rules", "",
        "1. A different hand count is evidence of detector-presence instability on the paired frames.",
        "2. A terminal-frame hold means the browser timestamp reached the container end while the final decoded source image was still displayed; these samples are explicitly flagged and are not treated as decoder failures.",
        "3. Equal hand count but different ordered handedness labels suggests classification/order instability; the labels are image-side labels, not confirmed anatomical identities.",
        "4. A changed production-vs-wrist-nearest mapping shows the two-hand detector-order policy would differ from a pose-wrist heuristic. It does not prove the heuristic is model-compatible.",
        "5. Coordinate drift is compared only for paired detections with a unique matching MediaPipe image-side handedness label. The per-hand CSV records full 21-point coordinate MAE/P95 after 1920/1080 scaling; this is a matching proxy, not proof of anatomical left/right identity.",
        "6. Interpolation summaries show how many missing coordinates are filled temporally; long missing runs may propagate across adjacent frames.",
        "", "## Safety and reproducibility", "",
        "- The classifier/checkpoint is not loaded and no recognition prediction is generated.",
        "- The official 77-item validation list is verified; no test split is used.",
        "- IoT remains hard-disabled with an empty mapping. No MQTT client or hardware command path is called.",
        "- `wrist_nearest` is strictly an offline counterfactual. No production setting is changed.",
        "- Browser/native timestamps are nearest-frame aligned, not pixel-synchronous. The maximum alignment error per clip is recorded in `phase3_summary.json`; exact half-frame ties select the later source frame.",
        "- Because frame sampling and MediaPipe tracking state can be sensitive to preceding frames, these results are diagnostic evidence, not a claim of physical-hand identity or definitive root cause.",
        "- Terminal end-of-stream samples are flagged in the per-frame CSV and summarized with their browser timestamps in the clip summary; they are not silently treated as exact half-frame matches.",
        "", "## Raw data", "",
        "See `phase3_per_frame_hand_diagnostics.csv`, `phase3_per_hand_coordinate_comparison.csv`, `phase3_clip_summary.csv`, and `phase3_summary.json` for frame-level values.", "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "phase3_hand_diagnostics")
    args = parser.parse_args()
    try:
        result = run(args)
        print(f"PHASE3_STATUS={result['status']}")
        print(f"CLIPS={result['successful_clip_count']}/{result['selected_clip_count']}")
        print(f"OUTPUT={args.output_dir.resolve()}")
        print("IoT remained disabled; classifier/checkpoint was not loaded.")
        return 0
    except Exception as exc:
        print(f"PHASE3_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
