#!/usr/bin/env python3
"""Phase 4: controlled replay to separate image transport from MediaPipe tracking.

Read-only offline experiment. Replays the six Phase 3 clips through the exact saved
browser JPEG frames and controlled transforms of the timestamp-matched native source
frames. Each variant uses a fresh MediaPipe session. No classifier/checkpoint or IoT.
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
    summarize_interpolation,
    write_csv,
)

VARIANTS = (
    # Exact actual Canvas frames; tracking and static-image modes isolate tracking history.
    {"name": "browser_canvas_tracking", "input": "browser_jpeg", "static": False},
    {"name": "browser_canvas_static", "input": "browser_jpeg", "static": True},
    # Native source-resolution frames; tracking and static-image modes.
    {"name": "native_original_tracking", "input": "native_original", "static": False},
    {"name": "native_original_static", "input": "native_original", "static": True},
    # Controlled resize to the exact browser JPEG dimensions, first lossless, then q92.
    {"name": "native_resized_png_tracking", "input": "native_png", "static": False},
    {"name": "native_resized_jpeg_q92_tracking", "input": "native_jpeg92", "static": False},
    {"name": "native_resized_jpeg_q92_static", "input": "native_jpeg92", "static": True},
)


def _labels_and_scores(handedness: list[Any]) -> tuple[list[str], list[float | None]]:
    labels: list[str] = []
    scores: list[float | None] = []
    for item in handedness:
        classifications = getattr(item, "classification", None) or []
        if classifications:
            c = classifications[0]
            labels.append(str(getattr(c, "label", "Unknown")))
            raw = getattr(c, "score", None)
            try:
                score = float(raw)
                scores.append(score if math.isfinite(score) else None)
            except (TypeError, ValueError):
                scores.append(None)
        else:
            labels.append("Unknown")
            scores.append(None)
    return labels, scores


def _prepare_frame(source: Any, input_mode: str, browser_bytes: bytes | None,
                   canvas_size: tuple[int, int], cv2: Any, np: Any) -> Any:
    """Prepare one input frame; native PNG/JPEG paths round-trip through codecs."""
    if input_mode == "browser_jpeg":
        if browser_bytes is None:
            raise RuntimeError("Browser JPEG bytes are missing")
        image = cv2.imdecode(np.frombuffer(browser_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Could not decode saved browser JPEG")
        return image
    if source is None:
        raise RuntimeError("Native source frame is missing")
    if input_mode == "native_original":
        return source
    resized = cv2.resize(source, canvas_size, interpolation=cv2.INTER_LINEAR)
    if input_mode == "native_png":
        ok, encoded = cv2.imencode(".png", resized)
    elif input_mode == "native_jpeg92":
        ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    else:
        raise ValueError(f"Unknown Phase 4 input mode: {input_mode}")
    if not ok:
        raise RuntimeError(f"Could not encode controlled {input_mode} frame")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None:
        raise RuntimeError(f"Could not decode controlled {input_mode} frame")
    return decoded


def _process_variant(name: str, input_mode: str, static_image_mode: bool,
                     browser_frames: tuple[bytes, ...], native_frames: list[Any],
                     timestamps: list[float], source_meta: dict[str, Any],
                     canvas_size: tuple[int, int], mp: Any, cv2: Any) -> dict[str, Any]:
    import numpy as np
    from isl_recognition.landmarks import landmark_results_to_feature_frame
    from isl_recognition.preprocess import make_scaled_frame_feature

    hands = mp.solutions.hands.Hands(
        static_image_mode=static_image_mode, max_num_hands=2,
        min_detection_confidence=0.5, min_tracking_confidence=0.5,
    )
    pose = mp.solutions.pose.Pose(
        static_image_mode=static_image_mode, min_detection_confidence=0.5,
        min_tracking_confidence=0.5, model_complexity=1,
    )
    rows: list[dict[str, Any]] = []
    features: list[np.ndarray] = []
    started = time.perf_counter()
    try:
        for idx, (browser_payload, native_frame, timestamp) in enumerate(
            zip(browser_frames, native_frames, timestamps)
        ):
            image = _prepare_frame(
                native_frame, input_mode,
                browser_payload if input_mode == "browser_jpeg" else None,
                canvas_size, cv2, np,
            )
            h, w = image.shape[:2]
            if input_mode != "native_original" and (w, h) != canvas_size:
                raise RuntimeError(
                    f"Variant {name} produced {w}x{h}; expected canvas size {canvas_size[0]}x{canvas_size[1]}"
                )
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            hand_result = hands.process(rgb)
            pose_result = pose.process(rgb)
            raw_hands = list(getattr(hand_result, "multi_hand_landmarks", None) or [])
            raw_handedness = list(getattr(hand_result, "multi_handedness", None) or [])
            labels, scores = _labels_and_scores(raw_handedness)
            mapped = landmark_results_to_feature_frame(
                hand_result, pose_result, "verified_one_hand_fix"
            )
            vec = make_scaled_frame_feature(
                mapped.pose_x, mapped.pose_y,
                mapped.hand1_x, mapped.hand1_y,
                mapped.hand2_x, mapped.hand2_y,
                allow_nan=True,
            ).astype(np.float32)
            if vec.shape != (134,) or np.isinf(vec).any():
                raise RuntimeError(f"Variant {name} produced invalid feature vector at frame {idx}")
            features.append(vec)

            mapped_slot_labels: dict[str, str | None] = {"hand1": None, "hand2": None}
            for slot, xs, ys in (
                ("hand1", mapped.hand1_x, mapped.hand1_y),
                ("hand2", mapped.hand2_x, mapped.hand2_y),
            ):
                if not xs or not ys or not math.isfinite(float(xs[0])) or not math.isfinite(float(ys[0])):
                    continue
                distances = []
                for det_i, hand in enumerate(raw_hands):
                    if not hand.landmark:
                        continue
                    d = math.hypot(
                        float(hand.landmark[0].x) - float(xs[0]),
                        float(hand.landmark[0].y) - float(ys[0]),
                    )
                    distances.append((d, det_i))
                if distances:
                    d, det_i = min(distances)
                    if d <= 1e-5 and det_i < len(labels):
                        mapped_slot_labels[slot] = labels[det_i]

            rows.append({
                "variant": name,
                "frame_index": idx,
                "browser_video_time_seconds": float(timestamp),
                "source_frame_index": source_meta["frame_indices"][idx],
                "timestamp_alignment_delta_ms": source_meta["alignment_delta_ms"][idx],
                "source_terminal_frame_hold": bool(source_meta["terminal_frame_hold_flags"][idx]),
                "frame_width": int(w), "frame_height": int(h),
                "pose_found": bool(mapped.pose_found),
                "detected_hand_count": len(raw_hands),
                "handedness_labels_in_detector_order": "|".join(labels),
                "handedness_labels_as_unordered_set": "|".join(sorted(labels)),
                "handedness_scores_in_detector_order": "|".join(
                    "" if score is None else f"{score:.6f}" for score in scores
                ),
                "hand1_slot_image_label": mapped_slot_labels["hand1"],
                "hand2_slot_image_label": mapped_slot_labels["hand2"],
                "hand1_feature_present_before_interpolation": bool(np.isfinite(vec[50:92]).all()),
                "hand2_feature_present_before_interpolation": bool(np.isfinite(vec[92:134]).all()),
                "missing_feature_coordinates_before_interpolation": int((~np.isfinite(vec)).sum()),
            })
    finally:
        hands.close()
        pose.close()

    stacked = np.stack(features)
    return {
        "variant": name,
        "input_mode": input_mode,
        "static_image_mode": static_image_mode,
        "frame_count": len(rows),
        "duration_seconds": time.perf_counter() - started,
        "pose_frames": sum(bool(r["pose_found"]) for r in rows),
        "frames_with_any_hand": sum(r["detected_hand_count"] >= 1 for r in rows),
        "frames_with_two_hands": sum(r["detected_hand_count"] == 2 for r in rows),
        "frames_with_one_hand": sum(r["detected_hand_count"] == 1 for r in rows),
        "hand1_slot_present_frames": sum(bool(r["hand1_feature_present_before_interpolation"]) for r in rows),
        "hand2_slot_present_frames": sum(bool(r["hand2_feature_present_before_interpolation"]) for r in rows),
        "mean_hand1_missing_fraction": float((~np.isfinite(stacked[:, 50:92])).mean()),
        "mean_hand2_missing_fraction": float((~np.isfinite(stacked[:, 92:134])).mean()),
        "mean_missing_feature_coordinates_per_frame": float((~np.isfinite(stacked)).sum(axis=1).mean()),
        "interpolation": summarize_interpolation(stacked),
        "frames": rows,
    }


def _comparison(left: dict[str, Any], right: dict[str, Any], clip: str) -> dict[str, Any]:
    a, b = left["frames"], right["frames"]
    comparable_count = ordered_matches = unordered_matches = 0
    for ra, rb in zip(a, b):
        if ra["detected_hand_count"] != rb["detected_hand_count"]:
            continue
        la = ra["handedness_labels_in_detector_order"]
        lb = rb["handedness_labels_in_detector_order"]
        # A no-hand/no-label pair is a hand-count match, not a handedness-label comparison.
        if not la or not lb:
            continue
        if len(la.split("|")) != len(lb.split("|")):
            continue
        comparable_count += 1
        if la == lb:
            ordered_matches += 1
        if ra["handedness_labels_as_unordered_set"] == rb["handedness_labels_as_unordered_set"]:
            unordered_matches += 1
    n = min(len(a), len(b))
    return {
        "video_path": clip,
        "baseline_variant": left["variant"],
        "comparison_variant": right["variant"],
        "paired_frames": n,
        "same_hand_count_frames": sum(
            ra["detected_hand_count"] == rb["detected_hand_count"] for ra, rb in zip(a, b)
        ),
        "same_hand_count_fraction": (
            sum(ra["detected_hand_count"] == rb["detected_hand_count"] for ra, rb in zip(a, b)) / n if n else None
        ),
        "label_comparable_frames_same_count": comparable_count,
        "ordered_label_match_frames": ordered_matches,
        "ordered_label_match_fraction": ordered_matches / comparable_count if comparable_count else None,
        "unordered_label_match_frames": unordered_matches,
        "unordered_label_match_fraction": unordered_matches / comparable_count if comparable_count else None,
    }


def _sha256(path: Path) -> str:
    return sha256_file(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    import cv2
    import mediapipe as mp
    import numpy as np
    from isl_recognition.app_config import Settings
    from isl_recognition.labels import load_label_map
    from isl_recognition.iot_protocol import load_label_commands

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 4 requires Python 3.12; found {sys.version.split()[0]}")
    if cv2.__version__ != "4.11.0" or mp.__version__ != "0.10.21":
        raise RuntimeError(f"Expected OpenCV 4.11.0/MediaPipe 0.10.21; found {cv2.__version__}/{mp.__version__}")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("ISL_IOT_ENABLED is true; Phase 4 is blocked")

    repo, data_root = args.repo.resolve(), args.data_root.resolve()
    manifest_path, capture_dir, outdir = args.manifest.resolve(), args.capture_dir.resolve(), args.output_dir.resolve()
    for path, label in ((repo, "AI4Bharat repo"), (data_root, "INCLUDE videos"),
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
    if len(capture_map) < 77:
        raise RuntimeError(f"Expected the complete saved browser capture set (77); found {len(capture_map)}")
    manifest_by_path = {norm_path(r["video_path"]): r for r in official_rows}
    outdir.mkdir(parents=True, exist_ok=True)

    frame_rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    clip_rows: list[dict[str, Any]] = []
    started_all = time.perf_counter()

    for relative, role in TARGETS.items():
        key = norm_path(relative)
        if key not in capture_map:
            raise RuntimeError(f"Missing Phase 3 evidence capture: {relative}")
        capture_path, parsed = capture_map[key]
        official = manifest_by_path[key]
        video_path = resolve_manifest_video(data_root, official["video_path"])
        timestamps = [float(r["video_time_seconds"]) for r in parsed.header["case"]["frames"]]
        native_frames, source_meta = _timestamp_matched_source(video_path, timestamps, cv2)
        first_browser = cv2.imdecode(np.frombuffer(parsed.frames[0], dtype=np.uint8), cv2.IMREAD_COLOR)
        if first_browser is None:
            raise RuntimeError(f"Cannot decode first browser frame for {relative}")
        canvas_size = (int(first_browser.shape[1]), int(first_browser.shape[0]))
        if canvas_size != (640, int(round(640 * int(parsed.header["case"]["source_height"]) / int(parsed.header["case"]["source_width"])) )):
            raise RuntimeError(f"Browser canvas dimensions disagree with capture header for {relative}: {canvas_size}")
        identity = {"video_path": relative, "expected_label": official["label"], "diagnostic_role": role,
                    "browser_capture_sha256": parsed.sha256, "source_video_sha256": _sha256(video_path)}

        current: dict[str, dict[str, Any]] = {}
        for spec in VARIANTS:
            result = _process_variant(
                spec["name"], spec["input"], bool(spec["static"]),
                parsed.frames, native_frames, timestamps, source_meta,
                canvas_size, mp, cv2,
            )
            current[spec["name"]] = result
            variant_rows.append({
                **identity,
                "variant": result["variant"],
                "input_mode": result["input_mode"],
                "static_image_mode": result["static_image_mode"],
                "frame_count": result["frame_count"],
                "pose_frames": result["pose_frames"],
                "frames_with_any_hand": result["frames_with_any_hand"],
                "frames_with_one_hand": result["frames_with_one_hand"],
                "frames_with_two_hands": result["frames_with_two_hands"],
                "hand1_slot_present_frames": result["hand1_slot_present_frames"],
                "hand2_slot_present_frames": result["hand2_slot_present_frames"],
                "mean_hand1_missing_fraction": result["mean_hand1_missing_fraction"],
                "mean_hand2_missing_fraction": result["mean_hand2_missing_fraction"],
                "mean_missing_feature_coordinates_per_frame": result["mean_missing_feature_coordinates_per_frame"],
                "processing_seconds": result["duration_seconds"],
                "interpolation_json": json.dumps(result["interpolation"], ensure_ascii=False),
            })
            for row in result["frames"]:
                frame_rows.append({**identity, **row})

        baseline = current["browser_canvas_tracking"]
        for spec in VARIANTS:
            if spec["name"] == "browser_canvas_tracking":
                continue
            pair_rows.append(_comparison(baseline, current[spec["name"]], relative))

        clip_rows.append({
            **identity,
            "browser_frame_count": len(parsed.frames),
            "source_fps": source_meta["fps"],
            "source_frame_count": source_meta["declared_frame_count"],
            "canvas_width": canvas_size[0], "canvas_height": canvas_size[1],
            "max_alignment_error_ms": source_meta["max_abs_alignment_delta_ms"],
            "terminal_frame_hold_count": source_meta["terminal_frame_hold_count"],
            "variant_count": len(current),
        })
        del current, native_frames

    result = {
        "campaign": "phase4_controlled_transport_tracking_replay",
        "status": "completed",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selected_clip_count": len(TARGETS),
        "successful_clip_count": len(clip_rows),
        "failed_clip_count": 0,
        "official_validation_split_sha256": split_hash,
        "official_repo_commit": commit,
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform,
                    "opencv": cv2.__version__, "mediapipe": mp.__version__,
                    "iot_enabled": False, "iot_mapping_count": 0,
                    "classifier_checkpoint_loaded": False, "production_code_modified": False},
        "methodology": {
            "browser_actual": "Exact JPEG bytes from the saved ISLBRW01 Canvas capture payloads",
            "native_source": "Timestamp-matched OpenCV-decoded original video frames; bounded terminal holds explicitly flagged",
            "native_resize": "OpenCV INTER_LINEAR resized to each clip's exact 640x363 browser frame dimensions",
            "png_path": "Lossless PNG encode/decode of the controlled resized native frame",
            "jpeg_path": "JPEG quality 92 encode/decode of the controlled resized native frame",
            "tracking_mode": "static_image_mode=False, fresh Hands/Pose session for each variant and clip",
            "static_mode": "static_image_mode=True, fresh Hands/Pose session for each variant and clip; diagnostic only",
            "slot_policy": "Existing verified_one_hand_fix; two-hand detector order preserved; no alternate mapping is applied",
            "comparison": "Every variant compared frame-by-frame to actual browser_canvas_tracking baseline; detector count, ordered labels and unordered label sets reported separately",
            "classifier": "Not loaded; no predictions, training, threshold tuning or checkpoint changes",
            "iot": "Disabled; no MQTT/network/action code invoked",
        },
        "variant_definitions": list(VARIANTS),
        "elapsed_seconds": time.perf_counter() - started_all,
        "clip_metadata": clip_rows,
        "files": {},
    }
    out_files = {
        "phase4_variant_clip_summary.csv": variant_rows,
        "phase4_per_frame_detector_diagnostics.csv": frame_rows,
        "phase4_pairwise_comparisons.csv": pair_rows,
        "phase4_clip_metadata.csv": clip_rows,
    }
    for name, rows in out_files.items():
        write_csv(outdir / name, rows)
    report = _build_report(result, variant_rows, pair_rows)
    (outdir / "PHASE4_CONTROLLED_REPLAY_REPORT.md").write_text(report, encoding="utf-8")
    result["files"] = {
        name: {"sha256": sha256_file(outdir / name), "bytes": (outdir / name).stat().st_size}
        for name in (*out_files.keys(), "PHASE4_CONTROLLED_REPLAY_REPORT.md")
    }
    (outdir / "phase4_summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _build_report(result: dict[str, Any], variant_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 4 — Controlled Transport and Tracking Replay", "",
        f"**Status:** {result['status']}",
        f"**Clips:** {result['successful_clip_count']}/{result['selected_clip_count']}",
        f"**Validation split SHA256:** `{result['official_validation_split_sha256']}`", "",
        "## Experimental paths", "",
        "| Variant | Input | MediaPipe mode |", "|---|---|---|",
    ]
    for v in VARIANTS:
        lines.append(f"| `{v['name']}` | `{v['input']}` | `{'static_image_mode=True' if v['static'] else 'static_image_mode=False'}` |")
    lines += ["", "## Per-clip detector results", "",
              "| Clip | Variant | Any-hand frames | Two-hand frames | Label slots present (1 / 2) |",
              "|---|---|---:|---:|---:|"]
    for r in variant_rows:
        lines.append(
            f"| `{Path(r['video_path']).name}` | `{r['variant']}` | "
            f"{r['frames_with_any_hand']}/{r['frame_count']} | "
            f"{r['frames_with_two_hands']}/{r['frame_count']} | "
            f"{r['hand1_slot_present_frames']}/{r['frame_count']} / {r['hand2_slot_present_frames']}/{r['frame_count']} |"
        )
    lines += ["", "## Comparison to actual browser Canvas tracking baseline", "",
              "The pairwise CSV keeps hand-count agreement separate from ordered handedness agreement and unordered label-set agreement. This is critical: `Left|Right` versus `Right|Left` is an order reversal, not a different label set.", "",
              "| Clip | Comparison variant | Same hand count | Ordered labels | Unordered label set |",
              "|---|---|---:|---:|---:|"]
    for r in pair_rows:
        n = r["paired_frames"]
        count = "n/a" if r["same_hand_count_fraction"] is None else f"{r['same_hand_count_fraction']:.1%} ({r['same_hand_count_frames']}/{n})"
        ordered = "n/a" if r["ordered_label_match_fraction"] is None else f"{r['ordered_label_match_fraction']:.1%} ({r['ordered_label_match_frames']}/{r['label_comparable_frames_same_count']})"
        unordered = "n/a" if r["unordered_label_match_fraction"] is None else f"{r['unordered_label_match_fraction']:.1%} ({r['unordered_label_match_frames']}/{r['label_comparable_frames_same_count']})"
        lines.append(f"| `{Path(r['video_path']).name}` | `{r['comparison_variant']}` | {count} | {ordered} | {unordered} |")
    lines += ["", "## Interpretation guide", "",
              "- If native resized JPEG/PNG detection approaches the actual browser result, input scaling/transport is implicated.",
              "- If actual browser tracking and actual browser static-image modes differ materially on identical JPEG frames, temporal tracking history is implicated.",
              "- If ordered labels disagree while unordered label sets agree, detector order changed while the detected handedness labels remained the same.",
              "- If static mode does not remove the order reversal, the issue is not explained solely by temporal tracking state.",
              "- These comparisons isolate mechanisms but do not establish which hand-slot convention the pretrained checkpoint expects.",
              "", "## Safety and reproducibility", "",
              "- Exact browser JPEG bytes are used for the browser variants.",
              "- Native resize uses OpenCV INTER_LINEAR to the exact capture dimensions; this is a controlled emulation, not a claim of pixel-identical browser Canvas resampling.",
              "- Fresh MediaPipe sessions are used for each clip/variant.",
              "- No classifier/checkpoint is loaded and no predictions are generated.",
              "- No production feature, slot policy, threshold, class order, model, or IoT setting is modified.",
              "- `wrist_nearest` is not applied; the existing production-compatible `verified_one_hand_fix` assignment remains the only slot mapping evaluated.",
              ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "phase4_controlled_replay")
    args = parser.parse_args()
    try:
        result = run(args)
        print(f"PHASE4_STATUS={result['status']}")
        print(f"CLIPS={result['successful_clip_count']}/{result['selected_clip_count']}")
        print(f"VARIANTS_PER_CLIP={len(VARIANTS)}")
        print(f"OUTPUT={args.output_dir.resolve()}")
        print("IoT remained disabled; classifier/checkpoint was not loaded; production code unchanged.")
        return 0
    except Exception as exc:
        print(f"PHASE4_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
