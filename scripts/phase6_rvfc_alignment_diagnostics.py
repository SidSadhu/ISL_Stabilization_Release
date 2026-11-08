#!/usr/bin/env python3
"""Phase 6: verify browser Canvas frame identity against source-video PTS.

Read-only diagnostic. Consumes six requestVideoFrameCallback captures, aligns
metadata.mediaTime to actual video presentation timestamps (ffprobe when
available; otherwise sequential OpenCV CAP_PROP_POS_MSEC), audits pixels around
that timestamp, and replays MediaPipe Hands in static-image mode. No classifier,
checkpoint, training, production-code mutation, or IoT.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for item in (ROOT / "src", SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from browser_capture_protocol import parse_capture_payload  # noqa: E402
from prepare_official_validation_manifest import (  # noqa: E402
    EXPECTED_VAL_COUNT,
    OFFICIAL_VAL_LIST_SHA256,
    PINNED_REPO_COMMIT,
    load_official_validation_rows,
)
from video_path_utils import resolve_manifest_video  # noqa: E402
from phase3_hand_assignment_diagnostics import TARGETS, norm_path, sha256_file, write_csv  # noqa: E402
from phase5_canvas_pixel_audit import (  # noqa: E402
    INTERPOLATIONS,
    _decode_browser_frame,
    pixel_metrics,
    resize_roundtrip,
)

TARGET_KEYS = {norm_path(path): role for path, role in TARGETS.items()}
OFFSETS = (-1, 0, 1)
DETECTOR_VARIANTS = ("browser_canvas", "native_pts_area_jpeg92", "native_pixel_best")


def nearest_pts_index(media_time: float, pts: list[float]) -> tuple[int, float]:
    """Return source frame nearest to media_time and signed (PTS - mediaTime)."""
    if not math.isfinite(media_time) or not pts:
        raise ValueError("media_time must be finite and PTS list must not be empty")
    index = min(range(len(pts)), key=lambda i: (abs(pts[i] - media_time), i))
    return index, float(pts[index] - media_time)


def summarize_timing(frame_meta: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize RVFC timing fields while tolerating no absent critical fields."""
    deltas = []
    presented = []
    callback = []
    media = []
    for row in frame_meta:
        mt = float(row["video_time_seconds"])
        ct = float(row["capture_current_time_seconds"])
        pf = int(row["presented_frames"])
        cb = float(row["callback_time_ms"])
        deltas.append((ct - mt) * 1000.0)
        media.append(mt)
        presented.append(pf)
        callback.append(cb)
    callback_gaps = [b - a for a, b in zip(callback, callback[1:])]
    presented_gaps = [b - a for a, b in zip(presented, presented[1:])]
    def med(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        n = len(ordered)
        return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {
        "frame_count": len(frame_meta),
        "current_time_minus_media_time_mean_ms": sum(deltas) / len(deltas) if deltas else None,
        "current_time_minus_media_time_median_ms": med(deltas),
        "current_time_minus_media_time_min_ms": min(deltas) if deltas else None,
        "current_time_minus_media_time_max_ms": max(deltas) if deltas else None,
        "media_time_monotonic": all(b > a for a, b in zip(media, media[1:])),
        "presented_frames_monotonic": all(b > a for a, b in zip(presented, presented[1:])),
        "presented_frame_gap_count_gt_one": sum(gap > 1 for gap in presented_gaps),
        "presented_frame_total_gap": sum(max(0, gap - 1) for gap in presented_gaps),
        "callback_gap_median_ms": med(callback_gaps),
        "callback_gap_max_ms": max(callback_gaps) if callback_gaps else None,
        "callback_interval_count_lt_35ms": sum(gap < 35 for gap in callback_gaps),
        "callback_interval_count_gt_60ms": sum(gap > 60 for gap in callback_gaps),
    }


def _ffprobe_pts(video_path: Path, ffprobe: str) -> list[float]:
    command = [
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_pts_time",
        "-of", "json", str(video_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video_path}: {completed.stderr[-800:]}")
    payload = json.loads(completed.stdout)
    result: list[float] = []
    for frame in payload.get("frames", []):
        raw = frame.get("best_effort_timestamp_time", frame.get("pkt_pts_time"))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            result.append(value)
    if not result:
        raise RuntimeError(f"ffprobe returned no finite frame PTS values for {video_path}")
    if any(b < a for a, b in zip(result, result[1:])):
        raise RuntimeError(f"ffprobe presentation timestamps are not monotonic for {video_path}")
    return result


def _opencv_pts(video_path: Path, cv2: Any) -> tuple[list[float], dict[str, Any]]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open source video for timestamp scan: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    declared_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    pts: list[float] = []
    try:
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            value = float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            if not math.isfinite(value) or value < 0:
                value = float("nan")
            pts.append(value)
            index += 1
    finally:
        cap.release()
    finite = [x for x in pts if math.isfinite(x)]
    valid = len(finite) == len(pts) and bool(pts) and all(b > a for a, b in zip(pts, pts[1:]))
    if not valid:
        if not math.isfinite(fps) or fps <= 0:
            raise RuntimeError(f"OpenCV could not provide monotonic frame timestamps and FPS is invalid: {video_path}")
        pts = [i / fps for i in range(len(pts))]
        mode = "opencv_nominal_fps_fallback"
    else:
        mode = "opencv_cap_pos_msec"
    return pts, {
        "fps_reported": fps,
        "declared_frame_count": declared_count,
        "decoded_frame_count": len(pts),
        "timestamp_mode": mode,
    }


def scan_source_pts(video_path: Path, cv2: Any, ffprobe: str | None) -> tuple[list[float], dict[str, Any]]:
    if ffprobe:
        try:
            pts = _ffprobe_pts(video_path, ffprobe)
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                raise RuntimeError(f"OpenCV cannot open source video: {video_path}")
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            declared = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            if declared > 0 and len(pts) != declared:
                raise RuntimeError(f"ffprobe/OpenCV frame-count mismatch: ffprobe={len(pts)}, OpenCV={declared}")
            return pts, {
                "fps_reported": fps,
                "declared_frame_count": declared,
                "decoded_frame_count": len(pts),
                "timestamp_mode": "ffprobe_best_effort_pts",
            }
        except Exception as exc:
            fallback_pts, meta = _opencv_pts(video_path, cv2)
            meta["ffprobe_fallback_reason"] = f"{type(exc).__name__}: {exc}"
            return fallback_pts, meta
    pts, meta = _opencv_pts(video_path, cv2)
    meta["ffprobe_available"] = False
    return pts, meta


def decode_selected_frames(video_path: Path, wanted: set[int], cv2: Any, expected_count: int) -> dict[int, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open source video for selected-frame decode: {video_path}")
    frames: dict[int, Any] = {}
    idx = 0
    try:
        while idx <= max(wanted, default=-1):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if idx in wanted:
                frames[idx] = frame
            idx += 1
    finally:
        cap.release()
    missing = sorted(wanted.difference(frames))
    if missing:
        raise RuntimeError(
            f"Could not decode PTS-selected source frames {missing[:10]} from {video_path}; "
            f"last_decoded_frame={idx - 1}; pts_frame_count={expected_count}"
        )
    return frames


def _static_detector_rows(video_path: str, browser_frames: tuple[bytes, ...], source_selected: list[Any],
                          source_area_jpeg: list[Any], pixel_best: list[Any], cv2: Any, mp: Any) -> list[dict[str, Any]]:
    import numpy as np
    variants = {
        "browser_canvas": [cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR) for payload in browser_frames],
        "native_pts_area_jpeg92": source_area_jpeg,
        "native_pts_best_pixel": pixel_best,
    }
    rows: list[dict[str, Any]] = []
    for variant, images in variants.items():
        hands = mp.solutions.hands.Hands(
            static_image_mode=True,
            max_num_hands=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        try:
            for frame_idx, image in enumerate(images):
                if image is None:
                    raise RuntimeError(f"Failed to decode {variant} frame {frame_idx} for {video_path}")
                result = hands.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                raw_hands = list(getattr(result, "multi_hand_landmarks", None) or [])
                raw_labels = list(getattr(result, "multi_handedness", None) or [])
                labels: list[str] = []
                scores: list[str] = []
                for item in raw_labels:
                    classifications = getattr(item, "classification", None) or []
                    if classifications:
                        cls = classifications[0]
                        labels.append(str(getattr(cls, "label", "Unknown")))
                        score = getattr(cls, "score", None)
                        scores.append("" if score is None else f"{float(score):.6f}")
                    else:
                        labels.append("Unknown")
                        scores.append("")
                rows.append({
                    "video_path": video_path,
                    "variant": variant,
                    "frame_index": frame_idx,
                    "detected_hand_count": len(raw_hands),
                    "handedness_labels_in_detector_order": "|".join(labels),
                    "handedness_labels_as_unordered_set": "|".join(sorted(labels)),
                    "handedness_scores_in_detector_order": "|".join(scores),
                })
        finally:
            hands.close()
    return rows


def _detector_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key[(row["video_path"], row["variant"])].append(row)
    output: list[dict[str, Any]] = []
    for video in sorted({r["video_path"] for r in rows}):
        base = by_key[(video, "browser_canvas")]
        for other_name in ("native_pts_area_jpeg92", "native_pts_best_pixel"):
            other = by_key[(video, other_name)]
            paired = min(len(base), len(other))
            same_count = ordered = unordered = comparable = 0
            for a, b in zip(base[:paired], other[:paired]):
                if a["detected_hand_count"] == b["detected_hand_count"]:
                    same_count += 1
                    if a["handedness_labels_in_detector_order"] and b["handedness_labels_in_detector_order"]:
                        comparable += 1
                        ordered += int(a["handedness_labels_in_detector_order"] == b["handedness_labels_in_detector_order"])
                        unordered += int(a["handedness_labels_as_unordered_set"] == b["handedness_labels_as_unordered_set"])
            output.append({
                "video_path": video,
                "baseline_variant": "browser_canvas",
                "comparison_variant": other_name,
                "paired_frames": paired,
                "same_hand_count_frames": same_count,
                "same_hand_count_fraction": same_count / paired if paired else None,
                "label_comparable_frames_same_count": comparable,
                "ordered_label_match_frames": ordered,
                "ordered_label_match_fraction": ordered / comparable if comparable else None,
                "unordered_label_match_frames": unordered,
                "unordered_label_match_fraction": unordered / comparable if comparable else None,
            })
    return output


def _read_phase5_offsets(phase5_dir: Path | None) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if not phase5_dir or not phase5_dir.is_dir():
        return output
    source = phase5_dir / "phase5_best_pixel_match_per_frame.csv"
    if not source.is_file():
        return output
    groups: dict[str, list[int]] = defaultdict(list)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = norm_path(row.get("video_path", ""))
            try:
                groups[key].append(int(row["best_source_frame_offset"]))
            except (KeyError, TypeError, ValueError):
                continue
    for key, values in groups.items():
        counts = Counter(values)
        total = len(values)
        output[key] = {
            "phase5_frames": total,
            "phase5_offset_minus_one_count": counts.get(-1, 0),
            "phase5_offset_zero_count": counts.get(0, 0),
            "phase5_offset_plus_one_count": counts.get(1, 0),
            "phase5_offset_minus_one_fraction": counts.get(-1, 0) / total if total else None,
        }
    return output


def analyze_clip(relative: str, role: str, official: dict[str, str], capture_file: Path, parsed: Any,
                 source_video: Path, cv2: Any, mp: Any, ffprobe: str | None,
                 phase5_stats: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    import numpy as np

    pts, source_meta = scan_source_pts(source_video, cv2, ffprobe)
    case = parsed.header["case"]
    frame_meta = case["frames"]
    browser_times = [float(row["presented_media_time_seconds"]) for row in frame_meta]
    nearest_indices: list[int] = []
    nearest_delta: list[float] = []
    for value in browser_times:
        index, delta = nearest_pts_index(value, pts)
        nearest_indices.append(index)
        nearest_delta.append(delta)
    need: set[int] = set()
    for index in nearest_indices:
        need.update(i for i in (index - 1, index, index + 1) if 0 <= i < len(pts))
    source_lookup = decode_selected_frames(source_video, need, cv2, len(pts))
    first_browser = _decode_browser_frame(parsed.frames[0], cv2)
    canvas_size = (int(first_browser.shape[1]), int(first_browser.shape[0]))
    expected_height = int(round(640 * int(case["source_height"]) / int(case["source_width"])))
    if canvas_size != (640, expected_height):
        raise RuntimeError(f"Unexpected RVFC Canvas dimensions for {relative}: {canvas_size}; expected {(640, expected_height)}")

    pixel_rows: list[dict[str, Any]] = []
    best_rows: list[dict[str, Any]] = []
    source_area_jpeg: list[Any] = []
    pixel_best_images: list[Any] = []
    timing_rows: list[dict[str, Any]] = []
    for i, (payload, metadata, nearest_idx, pts_delta) in enumerate(zip(parsed.frames, frame_meta, nearest_indices, nearest_delta)):
        browser = _decode_browser_frame(payload, cv2)
        area = resize_roundtrip(source_lookup[nearest_idx], canvas_size, "area", "jpeg92", cv2)
        source_area_jpeg.append(area)
        candidates = []
        for offset in OFFSETS:
            candidate_idx = nearest_idx + offset
            if candidate_idx not in source_lookup:
                continue
            for interpolation in INTERPOLATIONS:
                for codec in ("png", "jpeg92"):
                    candidate = resize_roundtrip(source_lookup[candidate_idx], canvas_size, interpolation, codec, cv2)
                    metrics = pixel_metrics(browser, candidate)
                    row = {
                        "video_path": relative,
                        "frame_index": i,
                        "presented_media_time_seconds": browser_times[i],
                        "capture_current_time_seconds": float(metadata["capture_current_time_seconds"]),
                        "current_time_minus_media_time_ms": (float(metadata["capture_current_time_seconds"]) - browser_times[i]) * 1000.0,
                        "nearest_pts_source_frame_index": nearest_idx,
                        "nearest_pts_source_frame_time_seconds": pts[nearest_idx],
                        "nearest_pts_minus_media_time_ms": pts_delta * 1000.0,
                        "candidate_source_frame_index": candidate_idx,
                        "candidate_offset_from_nearest_pts": offset,
                        "interpolation": interpolation,
                        "codec": codec,
                        **metrics,
                    }
                    pixel_rows.append(row)
                    candidates.append((row, candidate))
        best_row, best_image = min(candidates, key=lambda pair: (pair[0]["mae_bgr"], pair[0]["rmse_bgr"], abs(pair[0]["candidate_offset_from_nearest_pts"])))
        pixel_best_images.append(best_image)
        best_rows.append({
            "video_path": relative,
            "frame_index": i,
            "presented_media_time_seconds": browser_times[i],
            "nearest_pts_source_frame_index": nearest_idx,
            "nearest_pts_source_frame_time_seconds": pts[nearest_idx],
            "nearest_pts_minus_media_time_ms": pts_delta * 1000.0,
            "best_source_frame_index": best_row["candidate_source_frame_index"],
            "best_source_frame_offset_from_nearest_pts": best_row["candidate_offset_from_nearest_pts"],
            "best_interpolation": best_row["interpolation"],
            "best_codec": best_row["codec"],
            "best_mae_bgr": best_row["mae_bgr"],
            "best_rmse_bgr": best_row["rmse_bgr"],
            "best_psnr_db": best_row["psnr_db"],
        })
        timing_rows.append({
            "video_path": relative,
            "frame_index": i,
            "presented_media_time_seconds": browser_times[i],
            "capture_current_time_seconds": float(metadata["capture_current_time_seconds"]),
            "current_time_minus_media_time_ms": (float(metadata["capture_current_time_seconds"]) - browser_times[i]) * 1000.0,
            "presented_frames": int(metadata["presented_frames"]),
            "callback_time_ms": float(metadata["callback_time_ms"]),
            "expected_display_time_ms": metadata.get("expected_display_time_ms"),
            "processing_duration_ms": metadata.get("processing_duration_ms"),
            "canvas_encode_duration_ms": metadata.get("canvas_encode_duration_ms"),
            "elapsed_ms": float(metadata["elapsed_ms"]),
            "nearest_pts_source_frame_index": nearest_idx,
            "nearest_pts_source_frame_time_seconds": pts[nearest_idx],
            "nearest_pts_minus_media_time_ms": pts_delta * 1000.0,
        })

    detector_rows = _static_detector_rows(relative, parsed.frames, [source_lookup[i] for i in nearest_indices],
                                         source_area_jpeg, pixel_best_images, cv2, mp)
    clip_timing = summarize_timing(frame_meta)
    offset_counts = Counter(int(row["best_source_frame_offset_from_nearest_pts"]) for row in best_rows)
    meta = {
        "video_path": relative,
        "expected_label": official["label"],
        "diagnostic_role": role,
        "capture_file": capture_file.name,
        "capture_sha256": parsed.sha256,
        "source_video_sha256": sha256_file(source_video),
        "browser_frame_count": len(parsed.frames),
        "browser_reported_duration_seconds": case.get("source_duration_seconds"),
        "browser_source_width": case.get("source_width"),
        "browser_source_height": case.get("source_height"),
        "source_pts_mode": source_meta["timestamp_mode"],
        "source_fps_reported": source_meta.get("fps_reported"),
        "source_frame_count": len(pts),
        "source_declared_frame_count": source_meta.get("declared_frame_count"),
        "source_ffprobe_fallback_reason": source_meta.get("ffprobe_fallback_reason"),
        "nearest_pts_abs_delta_median_ms": sorted(abs(x) for x in nearest_delta)[len(nearest_delta) // 2] * 1000.0,
        "nearest_pts_abs_delta_max_ms": max(abs(x) for x in nearest_delta) * 1000.0,
        "best_pixel_offset_minus_one_count": offset_counts.get(-1, 0),
        "best_pixel_offset_zero_count": offset_counts.get(0, 0),
        "best_pixel_offset_plus_one_count": offset_counts.get(1, 0),
        "best_pixel_offset_minus_one_fraction": offset_counts.get(-1, 0) / len(best_rows),
        "pixel_mae_median": sorted(row["best_mae_bgr"] for row in best_rows)[len(best_rows) // 2],
        "pixel_mae_mean": sum(row["best_mae_bgr"] for row in best_rows) / len(best_rows),
        **clip_timing,
        **phase5_stats,
    }
    return meta, pixel_rows, best_rows, timing_rows, detector_rows


def build_report(summary: dict[str, Any], clip_rows: list[dict[str, Any]], detector_pairs: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 6 — requestVideoFrameCallback PTS alignment audit", "",
        f"Status: **{summary['status']}**", "",
        f"Processed {summary['selected_clip_count']} clips and {summary['browser_frame_count_total']} presented browser frames.",
        f"Pixel comparisons: {summary['pixel_comparison_count']}. Static detector rows: {summary['static_detector_row_count']}.", "",
        "## Method", "",
        "The new capture page draws the video synchronously inside `requestVideoFrameCallback` and stores `metadata.mediaTime`, `video.currentTime`, `metadata.presentedFrames`, callback timing, and Canvas encode duration for every JPEG.",
        "Source frame identity is matched to frame presentation timestamps. `ffprobe` best-effort PTS is preferred; if unavailable or rejected, the script records its fallback to sequential OpenCV timestamps or nominal FPS. A ±1-frame pixel search is then performed around the PTS-selected source frame.",
        "MediaPipe Hands is replayed in static-image mode for the saved browser JPEG, PTS-aligned native INTER_AREA/JPEG-92 frame, and best pixel-match native frame. No classifier or checkpoint is loaded.", "",
        "## Clip-level results", "",
        "| Clip | PTS source | Browser frames | Median currentTime−mediaTime | Median PTS−mediaTime | Best pixel offset −1 / 0 / +1 | Phase 5 −1 fraction | Median best MAE |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in clip_rows:
        lines.append(
            f"| `{Path(row['video_path']).name}` | {row['source_pts_mode']} | {row['browser_frame_count']} | "
            f"{row['current_time_minus_media_time_median_ms']:.3f} ms | {row['nearest_pts_abs_delta_median_ms']:.3f} ms | "
            f"{row['best_pixel_offset_minus_one_count']} / {row['best_pixel_offset_zero_count']} / {row['best_pixel_offset_plus_one_count']} | "
            f"{float(row.get('phase5_offset_minus_one_fraction', 0.0)):.1%} | {row['pixel_mae_median']:.3f} |"
        )
    lines.extend(["", "## Static MediaPipe comparison", "", "| Clip | Native variant | Hand-count same | Ordered label match | Unordered label match |", "|---|---|---:|---:|---:|"])
    for row in detector_pairs:
        def pct(key: str) -> str:
            value = row.get(key)
            return "n/a" if value is None else f"{100.0 * float(value):.1f}%"
        lines.append(
            f"| `{Path(row['video_path']).name}` | {row['comparison_variant']} | {pct('same_hand_count_fraction')} | "
            f"{pct('ordered_label_match_fraction')} | {pct('unordered_label_match_fraction')} |"
        )
    lines.extend([
        "", "## Interpretation guardrails", "",
        "- A browser `video.currentTime` offset does not by itself prove that the Canvas image is stale; the relevant identity is the callback's `metadata.mediaTime` paired with the synchronous draw.",
        "- A persistent −1 pixel-match offset after PTS alignment would require further investigation; pixel similarity alone cannot identify a presented frame conclusively when adjacent frames look alike.",
        "- If ffprobe was unavailable and nominal-FPS fallback was used, timestamp conclusions are provisional and must not be called definitive PTS validation.",
        "- This is a six-clip diagnostic, not a 77-clip recognition accuracy measurement.",
        "- No production setting, feature contract, checkpoint, class order, or IoT state was changed.",
    ])
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    import cv2
    import mediapipe as mp
    from isl_recognition.app_config import Settings
    from isl_recognition.labels import load_label_map
    from isl_recognition.iot_protocol import load_label_commands

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 6 requires Python 3.12; found {sys.version.split()[0]}")
    if cv2.__version__ != "4.11.0" or mp.__version__ != "0.10.21":
        raise RuntimeError(f"Expected OpenCV 4.11.0/MediaPipe 0.10.21; found {cv2.__version__}/{mp.__version__}")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("ISL_IOT_ENABLED is true; Phase 6 is blocked")

    repo = args.repo.resolve()
    data_root = args.data_root.resolve()
    manifest_path = args.manifest.resolve()
    capture_dir = args.capture_dir.resolve()
    outdir = args.output_dir.resolve()
    phase5_dir = args.phase5_dir.resolve() if args.phase5_dir else None
    for path, label in ((repo, "AI4Bharat repository"), (data_root, "INCLUDE video directory"),
                        (manifest_path, "official validation manifest"), (capture_dir, "Phase 6 captures")):
        if not path.exists():
            raise RuntimeError(f"Missing {label}: {path}")
    label_map = load_label_map(ROOT / "config" / "label_map_include50.json")
    official_rows, commit, split_hash = load_official_validation_rows(repo, set(label_map))
    if commit != PINNED_REPO_COMMIT or split_hash != OFFICIAL_VAL_LIST_SHA256 or len(official_rows) != EXPECTED_VAL_COUNT:
        raise RuntimeError("Pinned official validation split verification failed")
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as stream:
        supplied = list(csv.DictReader(stream))
    expected_pairs = {(norm_path(r["video_path"]), str(r["label"]).strip()) for r in official_rows}
    supplied_pairs = {(norm_path(r.get("video_path", "")), str(r.get("label", "")).strip()) for r in supplied}
    if len(supplied) != EXPECTED_VAL_COUNT or supplied_pairs != expected_pairs:
        raise RuntimeError("Manifest is not exactly the pinned official 77-item validation list")
    if any(r.get("split", "val") != "val" for r in supplied):
        raise RuntimeError("Non-validation manifest row detected")
    settings = Settings()
    settings.validate()
    if settings.pre_iot_lockdown is not True or settings.iot_default_enabled:
        raise RuntimeError("IoT lockdown is not safe")
    if load_label_commands(settings.iot_label_map_path, label_map):
        raise RuntimeError("IoT mapping must remain empty")

    official_by_path = {norm_path(r["video_path"]): r for r in official_rows}
    expected_targets = {key: official_by_path[key] for key in TARGET_KEYS}
    from phase3_hand_assignment_diagnostics import load_capture_map
    captures = load_capture_map(capture_dir, expected_targets)
    if set(captures) != set(expected_targets):
        missing = sorted(set(expected_targets).difference(captures))
        raise RuntimeError(f"Phase 6 requires the six exact RVFC captures; missing {missing}")
    for key, (_capture_path, parsed) in captures.items():
        cfg = parsed.header.get("capture_config", {})
        case = parsed.header.get("case", {})
        if (cfg.get("capture_sampling_mode") != "requestVideoFrameCallback"
                or cfg.get("capture_timestamp_field") != "metadata.mediaTime"
                or cfg.get("draw_synchronous_inside_callback") is not True):
            raise RuntimeError(f"Capture is not verified requestVideoFrameCallback evidence: {case.get('video_path', key)}")
        for frame_index, frame_meta in enumerate(case.get("frames", [])):
            required_fields = ("presented_media_time_seconds", "capture_current_time_seconds",
                               "presented_frames", "callback_time_ms")
            if any(field not in frame_meta for field in required_fields):
                raise RuntimeError(
                    f"RVFC timing metadata missing for {case.get('video_path', key)} frame {frame_index}"
                )
    outdir.mkdir(parents=True, exist_ok=True)
    ffprobe = shutil.which("ffprobe")
    phase5_stats = _read_phase5_offsets(phase5_dir)
    clip_rows: list[dict[str, Any]] = []
    pixel_rows: list[dict[str, Any]] = []
    best_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    detector_rows: list[dict[str, Any]] = []
    started = time.perf_counter()

    for relative, role in TARGETS.items():
        key = norm_path(relative)
        capture_file, parsed = captures[key]
        official = official_by_path[key]
        video_path = resolve_manifest_video(data_root, official["video_path"])
        phase5_stats_for_clip = phase5_stats.get(key, {})
        identity = {
            "video_path": relative,
            "expected_label": official["label"],
            "diagnostic_role": role,
            "capture_sha256": parsed.sha256,
            "source_video_sha256": sha256_file(video_path),
        }
        meta, pixels, best, timing, detector = analyze_clip(
            relative, role, official, capture_file, parsed, video_path,
            cv2, mp, ffprobe, phase5_stats_for_clip,
        )
        clip_rows.append({**identity, **meta})
        pixel_rows.extend({**identity, **row} for row in pixels)
        best_rows.extend({**identity, **row} for row in best)
        timing_rows.extend({**identity, **row} for row in timing)
        detector_rows.extend({**identity, **row} for row in detector)
        print(f"PHASE6_CLIP_OK={Path(relative).name} | frames={meta['browser_frame_count']} | PTS={meta['source_pts_mode']}", flush=True)

    detector_pairs = _detector_pairs(detector_rows)
    offset_counts = Counter(int(r["best_source_frame_offset_from_nearest_pts"]) for r in best_rows)
    summary = {
        "campaign": "phase6_rvfc_pts_alignment_audit",
        "status": "completed",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selected_clip_count": len(clip_rows),
        "successful_clip_count": len(clip_rows),
        "failed_clip_count": 0,
        "browser_frame_count_total": len(timing_rows),
        "pixel_comparison_count": len(pixel_rows),
        "static_detector_row_count": len(detector_rows),
        "best_pixel_offset_counts_from_nearest_pts": {str(k): offset_counts.get(k, 0) for k in OFFSETS},
        "ffprobe_available": bool(ffprobe),
        "ffprobe_path": ffprobe,
        "official_validation_split_sha256": split_hash,
        "official_repo_commit": commit,
        "capture_sampling_mode": "requestVideoFrameCallback",
        "capture_timestamp_field": "metadata.mediaTime",
        "draw_synchronous_inside_callback": True,
        "runtime": {
            "python": sys.version.split()[0], "platform": sys.platform,
            "opencv": cv2.__version__, "mediapipe": mp.__version__,
            "iot_enabled": False, "iot_mapping_count": 0,
            "classifier_checkpoint_loaded": False, "production_code_modified": False,
        },
        "methodology": {
            "source_timestamp_preference": "ffprobe best_effort_timestamp_time; fallback to OpenCV CAP_PROP_POS_MSEC; nominal FPS only if OpenCV timestamps are invalid",
            "pixel_candidates": {"source_offsets": list(OFFSETS), "resize_interpolations": list(INTERPOLATIONS), "codecs": ["png", "jpeg92"]},
            "static_detector": "MediaPipe Hands static_image_mode=True; max_num_hands=2; min_detection_confidence=0.5",
            "classifier": "Not loaded; no model inference, threshold tuning, training or checkpoint changes",
            "iot": "Disabled; no MQTT/network/action code invoked",
        },
        "elapsed_seconds": time.perf_counter() - started,
        "clip_metadata": clip_rows,
    }
    (outdir / "phase6_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_csv(outdir / "phase6_frame_timing.csv", timing_rows)
    write_csv(outdir / "phase6_frame_pixel_metrics.csv", pixel_rows)
    write_csv(outdir / "phase6_best_pixel_match_per_frame.csv", best_rows)
    write_csv(outdir / "phase6_clip_summary.csv", clip_rows)
    write_csv(outdir / "phase6_static_detector_frames.csv", detector_rows)
    write_csv(outdir / "phase6_static_detector_pairwise.csv", detector_pairs)
    (outdir / "PHASE6_RVFC_ALIGNMENT_REPORT.md").write_text(build_report(summary, clip_rows, detector_pairs), encoding="utf-8")
    print("PHASE6_STATUS=completed")
    print(f"CLIPS={len(clip_rows)}/6")
    print(f"BROWSER_FRAMES={len(timing_rows)}")
    print(f"PIXEL_COMPARISONS={len(pixel_rows)}")
    print(f"FFPROBE_AVAILABLE={bool(ffprobe)}")
    print(f"OUTPUT={outdir}")
    print("IoT remained disabled; classifier/checkpoint was not loaded; production code unchanged.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--phase5-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except Exception as exc:
        print(f"PHASE6_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
