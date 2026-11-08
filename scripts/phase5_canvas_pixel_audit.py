#!/usr/bin/env python3
"""Phase 5: pixel-level audit of actual browser Canvas JPEG frames.

Read-only offline diagnostic. Compares exact saved browser JPEG frames with
resized timestamp-matched native source frames across OpenCV interpolation,
PNG/JPEG round trips, and source-frame offsets (-1/0/+1). It then runs a
static-image MediaPipe Hands replay for the actual browser and selected native
transform variants. No classifier/checkpoint, training, threshold tuning, or IoT.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
import zipfile
from collections import defaultdict
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
from phase3_hand_assignment_diagnostics import (  # noqa: E402
    TARGETS,
    _timestamp_matched_source,
    load_capture_map,
    norm_path,
    sha256_file,
    write_csv,
)

INTERPOLATIONS = {
    "nearest": "INTER_NEAREST",
    "linear": "INTER_LINEAR",
    "area": "INTER_AREA",
    "cubic": "INTER_CUBIC",
    "lanczos4": "INTER_LANCZOS4",
}
CODECS = ("png", "jpeg92")
OFFSETS = (-1, 0, 1)

# These detector variants are deliberately static-image mode. Phase 4 already
# measured tracking effects; Phase 5 isolates image content from tracking state.
DETECTOR_VARIANTS = (
    {"name": "browser_canvas_static", "kind": "browser", "interpolation": None, "codec": None},
    {"name": "native_linear_png_static", "kind": "native", "interpolation": "linear", "codec": "png"},
    {"name": "native_nearest_jpeg92_static", "kind": "native", "interpolation": "nearest", "codec": "jpeg92"},
    {"name": "native_linear_jpeg92_static", "kind": "native", "interpolation": "linear", "codec": "jpeg92"},
    {"name": "native_area_jpeg92_static", "kind": "native", "interpolation": "area", "codec": "jpeg92"},
    {"name": "native_cubic_jpeg92_static", "kind": "native", "interpolation": "cubic", "codec": "jpeg92"},
    {"name": "native_lanczos4_jpeg92_static", "kind": "native", "interpolation": "lanczos4", "codec": "jpeg92"},
)


def pixel_metrics(reference: Any, candidate: Any) -> dict[str, float]:
    """Return robust full-frame BGR error metrics for equal uint8 images."""
    import numpy as np

    if reference is None or candidate is None:
        raise ValueError("Images cannot be None")
    if reference.shape != candidate.shape:
        raise ValueError(f"Image shape mismatch: {reference.shape} != {candidate.shape}")
    if reference.dtype != np.uint8 or candidate.dtype != np.uint8:
        raise ValueError("Pixel audit expects uint8 images")
    diff = candidate.astype(np.int16) - reference.astype(np.int16)
    abs_diff = np.abs(diff).astype(np.float32)
    sq = diff.astype(np.float32) ** 2
    mae = float(abs_diff.mean())
    rmse = float(np.sqrt(sq.mean()))
    psnr = 99.0 if rmse == 0 else float(20.0 * math.log10(255.0 / rmse))
    return {
        "mae_bgr": mae,
        "rmse_bgr": rmse,
        "psnr_db": psnr,
        "bias_b": float(diff[:, :, 0].mean()),
        "bias_g": float(diff[:, :, 1].mean()),
        "bias_r": float(diff[:, :, 2].mean()),
        "mae_b": float(abs_diff[:, :, 0].mean()),
        "mae_g": float(abs_diff[:, :, 1].mean()),
        "mae_r": float(abs_diff[:, :, 2].mean()),
    }


def resize_roundtrip(source: Any, size: tuple[int, int], interpolation_name: str,
                     codec: str, cv2: Any) -> Any:
    """Resize and codec-round-trip using explicit, reproducible OpenCV settings."""
    import numpy as np

    interpolation_attr = INTERPOLATIONS.get(interpolation_name)
    if interpolation_attr is None or not hasattr(cv2, interpolation_attr):
        raise ValueError(f"Unsupported interpolation: {interpolation_name}")
    resized = cv2.resize(source, size, interpolation=getattr(cv2, interpolation_attr))
    if codec == "raw":
        return resized
    if codec == "png":
        ok, encoded = cv2.imencode(".png", resized)
    elif codec == "jpeg92":
        ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    else:
        raise ValueError(f"Unsupported codec: {codec}")
    if not ok:
        raise RuntimeError(f"OpenCV failed to encode {codec} candidate")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None or decoded.dtype != np.uint8:
        raise RuntimeError(f"OpenCV failed to decode {codec} candidate")
    return decoded


def build_source_frame_lookup(video_path: Path, selected_frames: list[Any],
                              selected_indices: list[int], source_meta: dict[str, Any],
                              cv2: Any) -> tuple[dict[int, Any], int]:
    """Retain selected frames and decode only missing +/-1 neighbors.

    The full-resolution source frames are large. Reusing the timestamp-selected
    frames and keeping only a few missing neighbors avoids materializing a full
    video in RAM.
    """
    lookup: dict[int, Any] = {}
    for index, frame in zip(selected_indices, selected_frames):
        lookup[int(index)] = frame
    frame_count = int(source_meta["declared_frame_count"])
    wanted = {
        int(index + offset)
        for index in selected_indices
        for offset in OFFSETS
        if 0 <= int(index + offset) < frame_count
    }
    missing = wanted.difference(lookup)
    if not missing:
        return lookup, 0

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot reopen source video for neighbor frames: {video_path}")
    loaded_extra = 0
    frame_index = 0
    try:
        max_needed = max(missing)
        while frame_index <= max_needed:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if frame_index in missing:
                lookup[frame_index] = frame
                loaded_extra += 1
            frame_index += 1
    finally:
        cap.release()
    still_missing = sorted(missing.difference(lookup))
    if still_missing:
        raise RuntimeError(
            f"Could not decode neighboring source frames {still_missing[:8]} from {video_path}; "
            f"last_decoded_frame={frame_index - 1}"
        )
    return lookup, loaded_extra


def _decode_browser_frame(payload: bytes, cv2: Any) -> Any:
    import numpy as np

    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("Saved browser JPEG could not be decoded")
    return image


def _pixel_audit_for_clip(relative: str, parsed: Any, source_frame_lookup: dict[int, Any],
                          selected_indices: list[int], canvas_size: tuple[int, int], cv2: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frame_rows: list[dict[str, Any]] = []
    best_rows: list[dict[str, Any]] = []
    for frame_index, (jpeg_bytes, source_index) in enumerate(zip(parsed.frames, selected_indices)):
        browser = _decode_browser_frame(jpeg_bytes, cv2)
        if (browser.shape[1], browser.shape[0]) != canvas_size:
            raise RuntimeError(f"Browser frame size changed within clip {relative} at frame {frame_index}")
        candidates: list[dict[str, Any]] = []
        for offset in OFFSETS:
            source_target = source_index + offset
            if source_target not in source_frame_lookup:
                continue
            native = source_frame_lookup[source_target]
            for interpolation_name in INTERPOLATIONS:
                for codec in CODECS:
                    candidate = resize_roundtrip(native, canvas_size, interpolation_name, codec, cv2)
                    metrics = pixel_metrics(browser, candidate)
                    row = {
                        "video_path": relative,
                        "frame_index": frame_index,
                        "browser_video_time_seconds": float(parsed.header["case"]["frames"][frame_index]["video_time_seconds"]),
                        "selected_source_frame_index": int(source_index),
                        "candidate_source_frame_index": int(source_target),
                        "source_frame_offset": int(offset),
                        "interpolation": interpolation_name,
                        "codec": codec,
                        **metrics,
                    }
                    frame_rows.append(row)
                    candidates.append(row)
        if not candidates:
            raise RuntimeError(f"No pixel candidates generated for {relative} frame {frame_index}")
        best = min(candidates, key=lambda r: (r["mae_bgr"], r["rmse_bgr"], abs(r["source_frame_offset"])))
        best_rows.append({
            "video_path": relative,
            "frame_index": frame_index,
            "selected_source_frame_index": int(source_index),
            "best_source_frame_index": best["candidate_source_frame_index"],
            "best_source_frame_offset": best["source_frame_offset"],
            "best_interpolation": best["interpolation"],
            "best_codec": best["codec"],
            "best_mae_bgr": best["mae_bgr"],
            "best_rmse_bgr": best["rmse_bgr"],
            "best_psnr_db": best["psnr_db"],
        })
    return frame_rows, best_rows


def _detector_frames(name: str, variant: dict[str, Any], browser_frames: tuple[bytes, ...],
                     native_frames: list[Any], canvas_size: tuple[int, int], cv2: Any,
                     mp: Any, relative: str) -> list[dict[str, Any]]:
    hands = mp.solutions.hands.Hands(
        static_image_mode=True,
        max_num_hands=2,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    rows: list[dict[str, Any]] = []
    try:
        for idx, (payload, native) in enumerate(zip(browser_frames, native_frames)):
            if variant["kind"] == "browser":
                image = _decode_browser_frame(payload, cv2)
            else:
                image = resize_roundtrip(
                    native, canvas_size,
                    variant["interpolation"], variant["codec"], cv2,
                )
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            result = hands.process(rgb)
            raw_hands = list(getattr(result, "multi_hand_landmarks", None) or [])
            raw_labels = list(getattr(result, "multi_handedness", None) or [])
            labels: list[str] = []
            scores: list[float | None] = []
            for item in raw_labels:
                classes = getattr(item, "classification", None) or []
                if classes:
                    labels.append(str(getattr(classes[0], "label", "Unknown")))
                    try:
                        score = float(getattr(classes[0], "score", float("nan")))
                    except (TypeError, ValueError):
                        score = float("nan")
                    scores.append(score if math.isfinite(score) else None)
                else:
                    labels.append("Unknown")
                    scores.append(None)
            rows.append({
                "video_path": relative,
                "variant": name,
                "frame_index": idx,
                "frame_width": int(image.shape[1]),
                "frame_height": int(image.shape[0]),
                "detected_hand_count": len(raw_hands),
                "handedness_labels_in_detector_order": "|".join(labels),
                "handedness_labels_as_unordered_set": "|".join(sorted(labels)),
                "handedness_scores_in_detector_order": "|".join(
                    "" if value is None else f"{value:.6f}" for value in scores
                ),
            })
    finally:
        hands.close()
    return rows


def _summarize_detector_pair(base: list[dict[str, Any]], other: list[dict[str, Any]],
                             relative: str, variant: str) -> dict[str, Any]:
    paired = min(len(base), len(other))
    same_count = 0
    comparable = ordered = unordered = 0
    for a, b in zip(base, other):
        if a["detected_hand_count"] == b["detected_hand_count"]:
            same_count += 1
            la, lb = a["handedness_labels_in_detector_order"], b["handedness_labels_in_detector_order"]
            if la and lb:
                comparable += 1
                if la == lb:
                    ordered += 1
                if a["handedness_labels_as_unordered_set"] == b["handedness_labels_as_unordered_set"]:
                    unordered += 1
    return {
        "video_path": relative,
        "baseline_variant": "browser_canvas_static",
        "comparison_variant": variant,
        "paired_frames": paired,
        "same_hand_count_frames": same_count,
        "same_hand_count_fraction": same_count / paired if paired else None,
        "label_comparable_frames_same_count": comparable,
        "ordered_label_match_frames": ordered,
        "ordered_label_match_fraction": ordered / comparable if comparable else None,
        "unordered_label_match_frames": unordered,
        "unordered_label_match_fraction": unordered / comparable if comparable else None,
    }


def _aggregate_pixel_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["video_path"], row["interpolation"], row["codec"], row["source_frame_offset"])
        groups[key].append(row)
    out: list[dict[str, Any]] = []
    for (clip, interpolation, codec, offset), values in sorted(groups.items()):
        mae = sorted(float(x["mae_bgr"]) for x in values)
        rmse = sorted(float(x["rmse_bgr"]) for x in values)
        psnr = sorted(float(x["psnr_db"]) for x in values)
        n = len(values)
        out.append({
            "video_path": clip,
            "interpolation": interpolation,
            "codec": codec,
            "source_frame_offset": offset,
            "frame_count": n,
            "mean_mae_bgr": sum(mae) / n,
            "median_mae_bgr": mae[n // 2] if n % 2 else (mae[n // 2 - 1] + mae[n // 2]) / 2,
            "p95_mae_bgr": mae[min(n - 1, math.ceil(0.95 * n) - 1)],
            "mean_rmse_bgr": sum(rmse) / n,
            "mean_psnr_db": sum(psnr) / n,
        })
    return out


def _write_report(path: Path, summary: dict[str, Any], pixel_summary: list[dict[str, Any]],
                  detector_summary: list[dict[str, Any]], best_rows: list[dict[str, Any]]) -> None:
    by_clip_best: dict[str, dict[str, Any]] = {}
    for clip in sorted({r["video_path"] for r in best_rows}):
        rows = [r for r in best_rows if r["video_path"] == clip]
        offsets = {str(k): sum(1 for x in rows if x["best_source_frame_offset"] == k) for k in OFFSETS}
        by_clip_best[clip] = {"frames": len(rows), "best_offset_counts": offsets,
                              "median_best_mae": sorted(x["best_mae_bgr"] for x in rows)[len(rows)//2]}
    lines = [
        "# Phase 5 — Browser Canvas Pixel Audit",
        "",
        "**Status:** completed",
        f"**Clips:** {summary['selected_clip_count']}/{summary['selected_clip_count']}",
        f"**Browser frames audited:** {summary['browser_frame_count_total']}",
        f"**Pixel comparison rows:** {summary['pixel_comparison_rows']}",
        "",
        "## Method",
        "",
        "Each saved Canvas JPEG was decoded and compared with timestamp-matched native video frames. Native candidates were resized to the exact browser canvas dimensions using OpenCV nearest, linear, area, cubic, and Lanczos4 interpolation, then round-tripped through PNG or JPEG quality 92. Each candidate was also tested at source frame offsets -1, 0, and +1 as a diagnostic alignment search. The best offset is descriptive only; it does not alter the official timestamp alignment.",
        "",
        "A separate static-image MediaPipe Hands replay compared the actual browser frames with selected native resize/codec variants. Tracking was disabled deliberately to isolate image content. No classifier/checkpoint was loaded.",
        "",
        "## Best pixel matches by clip",
        "",
        "| Clip | Frames | Best-offset counts (-1 / 0 / +1) | Median best MAE |",
        "|---|---:|---:|---:|",
    ]
    for clip, row in by_clip_best.items():
        counts = row["best_offset_counts"]
        lines.append(f"| `{Path(clip).name}` | {row['frames']} | {counts['-1']} / {counts['0']} / {counts['1']} | {row['median_best_mae']:.3f} |")
    lines.extend(["", "## Best transform by clip (median absolute pixel error)", "",
                  "| Clip | Interpolation | Codec | Source offset | Mean MAE | Median MAE | Mean PSNR |",
                  "|---|---|---|---:|---:|---:|---:|"])
    for clip in sorted({r["video_path"] for r in pixel_summary}):
        candidates = [r for r in pixel_summary if r["video_path"] == clip]
        best = min(candidates, key=lambda x: (x["median_mae_bgr"], x["mean_mae_bgr"], abs(x["source_frame_offset"])))
        lines.append(f"| `{Path(clip).name}` | {best['interpolation']} | {best['codec']} | {best['source_frame_offset']:+d} | {best['mean_mae_bgr']:.3f} | {best['median_mae_bgr']:.3f} | {best['mean_psnr_db']:.2f} |")
    lines.extend(["", "## Static MediaPipe detector comparison", "",
                  "| Clip | Variant | Same hand count | Ordered labels | Unordered labels |",
                  "|---|---|---:|---:|---:|"])
    for row in detector_summary:
        same = "n/a" if row["same_hand_count_fraction"] is None else f"{row['same_hand_count_fraction']*100:.1f}% ({row['same_hand_count_frames']}/{row['paired_frames']})"
        ordered = "n/a" if row["ordered_label_match_fraction"] is None else f"{row['ordered_label_match_fraction']*100:.1f}% ({row['ordered_label_match_frames']}/{row['label_comparable_frames_same_count']})"
        unordered = "n/a" if row["unordered_label_match_fraction"] is None else f"{row['unordered_label_match_fraction']*100:.1f}% ({row['unordered_label_match_frames']}/{row['label_comparable_frames_same_count']})"
        lines.append(f"| `{Path(row['video_path']).name}` | {row['comparison_variant']} | {same} | {ordered} | {unordered} |")
    lines.extend([
        "",
        "## Interpretation boundaries",
        "",
        "- A non-zero best source-frame offset indicates that a neighboring decoded source frame may be visually closer to the saved browser JPEG. It does not prove the browser displayed that source frame; the browser's decoded frame is not directly exposed by the capture bundle beyond its JPEG and timestamp.",
        "- Pixel MAE/PSNR do not measure gesture correctness. Detector parity is reported separately.",
        "- OpenCV resizing and JPEG encoding are controlled approximations, not pixel-identical implementations of the browser Canvas/codec path.",
        "- MediaPipe static-image results isolate per-frame image content but do not reproduce tracking history.",
        "- No production recommendation should be made from six diagnostic clips alone. A candidate must subsequently be checked on the frozen 77-clip validation split before any release change.",
        "",
        "## Safety and reproducibility",
        "",
        f"- Official validation-list SHA-256: `{summary['official_validation_split_sha256']}`",
        f"- Pinned AI4Bharat commit: `{summary['official_repo_commit']}`",
        "- Python 3.12; OpenCV 4.11.0; MediaPipe 0.10.21.",
        "- IoT disabled; classifier/checkpoint not loaded; production files unchanged.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha256_manifest(root: Path) -> None:
    rows = []
    for file in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"):
        rows.append(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(root).as_posix()}")
    (root / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    import cv2
    import mediapipe as mp
    from isl_recognition.app_config import Settings
    from isl_recognition.labels import load_label_map
    from isl_recognition.iot_protocol import load_label_commands

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 5 requires Python 3.12; found {sys.version.split()[0]}")
    if cv2.__version__ != "4.11.0" or mp.__version__ != "0.10.21":
        raise RuntimeError(f"Expected OpenCV 4.11.0/MediaPipe 0.10.21; found {cv2.__version__}/{mp.__version__}")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("ISL_IOT_ENABLED is true; Phase 5 is blocked")

    repo, data_root = args.repo.resolve(), args.data_root.resolve()
    manifest_path, capture_dir, outdir = args.manifest.resolve(), args.capture_dir.resolve(), args.output_dir.resolve()
    for path, label in ((repo, "AI4Bharat repository"), (data_root, "INCLUDE videos"),
                        (manifest_path, "official validation manifest"), (capture_dir, "browser captures")):
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
        raise RuntimeError("Non-validation row detected in manifest")

    settings = Settings()
    settings.validate()
    if settings.pre_iot_lockdown is not True or settings.iot_default_enabled:
        raise RuntimeError("IoT lockdown settings are not safe")
    if load_label_commands(settings.iot_label_map_path, label_map):
        raise RuntimeError("IoT label mapping must remain empty")

    expected_by_path = {norm_path(r["video_path"]): r for r in official_rows}
    capture_map = load_capture_map(capture_dir, expected_by_path)
    if len(capture_map) < EXPECTED_VAL_COUNT:
        raise RuntimeError(f"Expected all 77 saved browser captures; found {len(capture_map)}")
    manifest_by_path = {norm_path(r["video_path"]): r for r in official_rows}
    outdir.mkdir(parents=True, exist_ok=True)

    pixel_rows: list[dict[str, Any]] = []
    best_pixel_rows: list[dict[str, Any]] = []
    detector_rows: list[dict[str, Any]] = []
    detector_pair_rows: list[dict[str, Any]] = []
    clip_rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    total_browser_frames = 0

    for relative, role in TARGETS.items():
        key = norm_path(relative)
        if key not in capture_map:
            raise RuntimeError(f"Missing Phase 3/4 evidence capture: {relative}")
        capture_path, parsed = capture_map[key]
        official = manifest_by_path[key]
        video_path = resolve_manifest_video(data_root, official["video_path"])
        timestamps = [float(r["video_time_seconds"]) for r in parsed.header["case"]["frames"]]
        selected_native_frames, source_meta = _timestamp_matched_source(video_path, timestamps, cv2)
        selected_indices = list(source_meta["frame_indices"])
        source_frame_lookup, extra_neighbor_frames = build_source_frame_lookup(
            video_path, selected_native_frames, selected_indices, source_meta, cv2
        )
        first_browser = _decode_browser_frame(parsed.frames[0], cv2)
        canvas_size = (int(first_browser.shape[1]), int(first_browser.shape[0]))
        if canvas_size != (640, int(round(640 * int(parsed.header["case"]["source_height"]) / int(parsed.header["case"]["source_width"])) )):
            raise RuntimeError(f"Browser canvas dimensions disagree with capture header for {relative}: {canvas_size}")
        identity = {
            "video_path": relative,
            "expected_label": official["label"],
            "diagnostic_role": role,
            "browser_capture_sha256": parsed.sha256,
            "source_video_sha256": sha256_file(video_path),
        }

        px, best = _pixel_audit_for_clip(relative, parsed, source_frame_lookup,
                                         selected_indices, canvas_size, cv2)
        for row in px:
            pixel_rows.append({**identity, **row})
        for row in best:
            best_pixel_rows.append({**identity, **row})

        current_detector: dict[str, list[dict[str, Any]]] = {}
        for variant in DETECTOR_VARIANTS:
            name = variant["name"]
            rows = _detector_frames(name, variant, parsed.frames, selected_native_frames,
                                    canvas_size, cv2, mp, relative)
            current_detector[name] = rows
            detector_rows.extend({**identity, **row} for row in rows)
        baseline = current_detector["browser_canvas_static"]
        for variant in DETECTOR_VARIANTS:
            if variant["name"] == "browser_canvas_static":
                continue
            detector_pair_rows.append(_summarize_detector_pair(
                baseline, current_detector[variant["name"]], relative, variant["name"]
            ))

        clip_rows.append({
            **identity,
            "browser_frame_count": len(parsed.frames),
            "source_fps": source_meta["fps"],
            "source_frame_count_declared": source_meta["declared_frame_count"],
            "neighbor_frames_loaded_extra": extra_neighbor_frames,
            "source_neighbor_lookup_count": len(source_frame_lookup),
            "canvas_width": canvas_size[0],
            "canvas_height": canvas_size[1],
            "max_alignment_error_ms": source_meta["max_abs_alignment_delta_ms"],
            "terminal_frame_hold_count": source_meta["terminal_frame_hold_count"],
            "diagnostic_variant_count": len(DETECTOR_VARIANTS),
        })
        total_browser_frames += len(parsed.frames)
        del source_frame_lookup, selected_native_frames, current_detector

    pixel_summary = _aggregate_pixel_rows(pixel_rows)
    summary = {
        "campaign": "phase5_browser_canvas_pixel_audit",
        "status": "completed",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selected_clip_count": len(clip_rows),
        "successful_clip_count": len(clip_rows),
        "failed_clip_count": 0,
        "browser_frame_count_total": total_browser_frames,
        "pixel_comparison_rows": len(pixel_rows),
        "static_detector_rows": len(detector_rows),
        "official_validation_split_sha256": split_hash,
        "official_repo_commit": commit,
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform,
                    "opencv": cv2.__version__, "mediapipe": mp.__version__,
                    "iot_enabled": False, "iot_mapping_count": 0,
                    "classifier_checkpoint_loaded": False, "production_code_modified": False},
        "methodology": {
            "browser_input": "Exact JPEG bytes parsed from saved ISLBRW01 Canvas captures",
            "source_alignment": "Nearest source frame at browser video_time_seconds; offsets -1/0/+1 used only for pixel similarity search",
            "resize_candidates": list(INTERPOLATIONS),
            "codec_candidates": list(CODECS),
            "pixel_metrics": ["MAE BGR", "RMSE BGR", "PSNR", "per-channel MAE", "per-channel signed bias"],
            "static_detector": "MediaPipe Hands static_image_mode=True; max_num_hands=2; min_detection_confidence=0.5; no temporal tracking",
            "slot_policy": "No feature-slot assignment or classifier feature vector is produced in Phase 5",
            "classifier": "Not loaded; no predictions, training, threshold tuning, or checkpoint changes",
            "iot": "Disabled; no MQTT/network/action code invoked",
        },
        "elapsed_seconds": time.perf_counter() - started,
        "clip_metadata": clip_rows,
    }

    files = {
        "phase5_summary.json": summary,
    }
    for name, payload in files.items():
        (outdir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(outdir / "phase5_frame_pixel_metrics.csv", pixel_rows)
    write_csv(outdir / "phase5_pixel_transform_summary.csv", pixel_summary)
    write_csv(outdir / "phase5_best_pixel_match_per_frame.csv", best_pixel_rows)
    write_csv(outdir / "phase5_static_detector_frames.csv", detector_rows)
    write_csv(outdir / "phase5_static_detector_pairwise.csv", detector_pair_rows)
    write_csv(outdir / "phase5_clip_metadata.csv", clip_rows)
    _write_report(outdir / "PHASE5_CANVAS_PIXEL_AUDIT_REPORT.md", summary,
                  pixel_summary, detector_pair_rows, best_pixel_rows)

    print("PHASE5_STATUS=completed")
    print(f"CLIPS={len(clip_rows)}/{len(TARGETS)}")
    print(f"BROWSER_FRAMES={total_browser_frames}")
    print(f"PIXEL_COMPARISONS={len(pixel_rows)}")
    print(f"STATIC_DETECTOR_VARIANTS={len(DETECTOR_VARIANTS)}")
    print(f"OUTPUT={outdir}")
    print("IoT remained disabled; classifier/checkpoint was not loaded; production code unchanged.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PHASE5_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
