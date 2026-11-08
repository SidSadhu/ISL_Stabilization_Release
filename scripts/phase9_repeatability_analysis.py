#!/usr/bin/env python3
"""Analyze Phase 9 first-paused-frame recovery and repeated encoder captures."""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import parse_capture_payload, normalize_video_path  # noqa: E402
from serve_browser_validation_capture import read_and_verify_manifest  # noqa: E402

TARGET_PATHS = [
    "Greetings/51. Good Morning/MVI_0099.MOV",
    "Colours/55. White/MVI_5063.MOV",
    "Adjectives/83. big large/MVI_9396.MOV",
    "Adjectives/79. short/MVI_9302.MOV",
    "Means_of_Transportation/11. Car/MVI_3118.MOV",
    "Pronouns/40. I/MVI_9922.MOV",
]
ENCODERS = ("sync", "async")
REPEATS = (1, 2, 3)


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def percentile(values: list[float], p: float) -> float | None:
    clean = sorted(float(x) for x in values if finite(x))
    if not clean:
        return None
    return clean[min(len(clean) - 1, max(0, math.ceil(p * len(clean)) - 1))]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ffprobe_pts(ffprobe: Path, source: Path) -> tuple[list[float], dict[str, Any]]:
    command = [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_frames",
               "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise RuntimeError(f"FFprobe failed for {source.name}: {result.stderr[-1200:]}")
    payload = json.loads(result.stdout)
    pts = [float(frame["best_effort_timestamp_time"]) for frame in payload.get("frames", [])
           if frame.get("best_effort_timestamp_time") not in (None, "N/A")]
    if len(pts) < 12:
        raise ValueError(f"FFprobe returned only {len(pts)} usable frame timestamps for {source}")
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError(f"FFprobe timestamps are not strictly increasing for {source}")
    steps = [(b - a) * 1000 for a, b in zip(pts, pts[1:])]
    median_step = statistics.median(steps)
    return pts, {
        "source_frame_count": len(pts), "first_best_effort_pts_seconds": pts[0],
        "last_best_effort_pts_seconds": pts[-1], "pts_step_median_ms": median_step,
        "pts_step_min_ms": min(steps), "pts_step_max_ms": max(steps),
        "source_video_sha256": sha256_file(source), "pts_source": "ffprobe_best_effort_timestamp_time",
    }


def locate_source(data_root: Path, video_path: str) -> Path:
    normalized = normalize_video_path(video_path)
    expected = data_root.joinpath(*normalized.split("/"))
    if expected.is_file():
        return expected
    stem = Path(normalized).stem.casefold()
    matches = [p for p in data_root.rglob("*") if p.is_file() and p.stem.casefold() == stem]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        parent = tuple(part.casefold() for part in Path(*normalized.split("/")).parent.parts)
        narrowed = [p for p in matches if tuple(part.casefold() for part in p.relative_to(data_root).parts[-(len(parent)+1):-1]) == parent]
        if len(narrowed) == 1:
            return narrowed[0]
    raise FileNotFoundError(f"Could not uniquely resolve {video_path}; same-stem matches={len(matches)}")


def nearest_index(pts: list[float], timestamp: float) -> tuple[int, float]:
    position = bisect.bisect_left(pts, timestamp)
    candidates = [idx for idx in (position - 1, position) if 0 <= idx < len(pts)]
    index = min(candidates, key=lambda idx: abs(pts[idx] - timestamp))
    return index, timestamp - pts[index]


def missing_ranges(indexes: set[int], count: int) -> list[list[int]]:
    missing = sorted(set(range(count)) - indexes)
    ranges: list[list[int]] = []
    for value in missing:
        if not ranges or value > ranges[-1][-1] + 1:
            ranges.append([value, value])
        else:
            ranges[-1][-1] = value
    return ranges


def jaccard(left: set[int], right: set[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_capture(run_dir: Path, expected_row: dict[str, str], encoder: str, repeat: int, run_id: str) -> dict[str, Any]:
    index_path = run_dir / "capture_index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"Capture index missing: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("official_split_count") != 77 or index.get("received_count") != 6:
        raise ValueError(f"{run_id}: capture index must contain six of the pinned 77 validation rows")
    config = index.get("capture_config", {})
    if (config.get("phase9_encoder"), config.get("phase9_repeat"), config.get("phase9_run_id")) != (encoder, repeat, run_id):
        raise ValueError(f"{run_id}: capture index identity mismatch")
    wanted = normalize_video_path(expected_row["video_path"]).casefold()
    records = {normalize_video_path(item["video_path"]).casefold(): item for item in index.get("received", [])}
    if wanted not in records:
        raise FileNotFoundError(f"{run_id}: missing {expected_row['video_path']}")
    record = records[wanted]
    relative = Path(record.get("container", ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe capture path: {relative}")
    container = (run_dir / relative).resolve()
    container.relative_to(run_dir.resolve())
    payload = container.read_bytes()
    parsed = parse_capture_payload(payload, expected_row)
    if parsed.sha256 != record.get("sha256"):
        raise ValueError(f"Capture SHA-256 mismatch: {container}")
    case = parsed.header["case"]
    cfg = parsed.header["capture_config"]
    identity = (case.get("phase9_encoder"), case.get("phase9_repeat"), case.get("phase9_run_id"))
    config_identity = (cfg.get("phase9_encoder"), cfg.get("phase9_repeat"), cfg.get("phase9_run_id"))
    if identity != (encoder, repeat, run_id) or config_identity != identity:
        raise ValueError(f"{run_id}: payload identity mismatch for {expected_row['video_path']}")
    return {"case": case, "frames": parsed.frames, "sha256": parsed.sha256, "container": str(container)}


def analyze_capture(encoder: str, repeat: int, case: dict[str, Any], pts: list[float], pts_meta: dict[str, Any], jpeg_payloads: tuple[bytes, ...] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    frames = case["frames"]
    mapped: list[tuple[int, float, dict[str, Any]]] = []
    frame_rows: list[dict[str, Any]] = []
    for idx, frame in enumerate(frames):
        source_index, delta = nearest_index(pts, float(frame["video_time_seconds"]))
        mapped.append((source_index, delta, frame))
        frame_rows.append({
            "encoder": encoder, "repeat": repeat, "video_path": case["video_path"], "label": case["label"],
            "capture_frame_index": idx, "capture_kind": frame.get("capture_kind", "unknown"),
            "media_time_seconds": frame["video_time_seconds"], "source_pts_frame_index": source_index,
            "source_best_effort_pts_seconds": pts[source_index], "capture_minus_pts_ms": delta * 1000,
            "presented_frames_counter": frame.get("presented_frames"), "elapsed_ms": frame.get("elapsed_ms"),
            "callback_time_ms": frame.get("callback_time_ms"), "callback_work_ms": frame.get("callback_work_ms"),
            "draw_duration_ms": frame.get("draw_duration_ms"), "encode_latency_ms": frame.get("encode_latency_ms"),
            "pending_encodes_at_enqueue": frame.get("pending_encodes_at_enqueue"),
            "visibility_state": frame.get("visibility_state"), "has_focus": frame.get("has_focus"),
            "jpeg_bytes": frame.get("jpeg_bytes"),
            "jpeg_sha256": hashlib.sha256(jpeg_payloads[idx]).hexdigest() if jpeg_payloads is not None and idx < len(jpeg_payloads) else None,
        })
    indexes = [item[0] for item in mapped]
    unique = set(indexes)
    duplicates = len(indexes) - len(unique)
    ranges = missing_ranges(unique, len(pts))
    abs_delta_ms = [abs(item[1]) * 1000 for item in mapped]
    half_frame_ms = float(pts_meta["pts_step_median_ms"]) / 2
    initial_records = [i for i, row in enumerate(frames) if row.get("capture_kind") == "initial_paused"]
    first_is_initial = bool(frames and frames[0].get("capture_kind") == "initial_paused")
    initial_timestamp_zero = bool(frames and abs(float(frames[0].get("video_time_seconds", -1))) <= 1e-9)
    initial_video_paused = bool(frames and frames[0].get("video_paused") is True
                                and abs(float(frames[0].get("capture_current_time_seconds", -1))) <= 1e-9)
    initial_maps_to_zero = bool(mapped and mapped[0][0] == 0 and abs(mapped[0][1] * 1000) <= half_frame_ms + 1e-6)
    timeline_types = [str(event.get("event_type", "")) for event in case.get("timeline_events", [])]
    initial_event_before_play = bool("initial_paused_frame_captured" in timeline_types and "play_invoked" in timeline_types
                                    and timeline_types.index("initial_paused_frame_captured") < timeline_types.index("play_invoked"))
    callback_frames = [row for row in frames if row.get("capture_kind") == "rvfc_playback"]
    callback_gaps = [float(b["callback_time_ms"]) - float(a["callback_time_ms"]) for a, b in zip(callback_frames, callback_frames[1:])
                     if finite(a.get("callback_time_ms")) and finite(b.get("callback_time_ms"))]
    encodes = [float(row["encode_latency_ms"]) for row in frames if finite(row.get("encode_latency_ms"))]
    presented = [int(row["presented_frames"]) for row in callback_frames if row.get("presented_frames") is not None]
    presented_gaps = [max(0, b - a - 1) for a, b in zip(presented, presented[1:])]
    summary = {
        "encoder": encoder, "repeat": repeat, "run_id": case.get("phase9_run_id"),
        "video_path": case["video_path"], "label": case["label"],
        "source_frame_count": len(pts), "captured_frame_count": len(frames),
        "unique_source_frames_captured": len(unique), "source_frame_coverage_pct": 100 * len(unique) / len(pts),
        "duplicate_source_frame_matches": duplicates, "missing_source_frame_count": len(pts) - len(unique),
        "missing_source_frame_ranges": ranges, "first_source_frame_index": min(unique) if unique else None,
        "last_source_frame_index": max(unique) if unique else None,
        "leading_source_frames_missing": min(unique) if unique else len(pts),
        "trailing_source_frames_missing": len(pts) - 1 - max(unique) if unique else len(pts),
        "initial_paused_frame_count": len(initial_records), "first_record_is_initial_paused": first_is_initial,
        "initial_frame_timestamp_zero": initial_timestamp_zero, "initial_frame_video_paused_at_zero": initial_video_paused,
        "initial_frame_event_precedes_play": initial_event_before_play,
        "initial_frame_maps_to_source_index_zero": initial_maps_to_zero,
        "initial_frame_timestamp_seconds": frames[0].get("video_time_seconds") if frames else None,
        "pts_match_within_half_frame_pct": 100 * sum(x <= half_frame_ms + 1e-6 for x in abs_delta_ms) / max(1, len(abs_delta_ms)),
        "pts_abs_delta_median_ms": statistics.median(abs_delta_ms) if abs_delta_ms else None,
        "rVFC_requested_count": int(case.get("callback_requested_count", 0)),
        "rVFC_delivered_count": int(case.get("callback_delivered_count", 0)),
        "callbacks_scheduled_not_delivered": int(case.get("callbacks_scheduled_not_delivered", 0)),
        "duplicate_media_callbacks": int(case.get("duplicate_media_callback_skips", 0)),
        "presentedFrames_interior_gap_total": sum(presented_gaps),
        "presentedFrames_max_interior_gap": max(presented_gaps, default=0),
        "capture_duration_ms": case.get("capture_duration_ms"),
        "last_frame_elapsed_ms": max((float(row.get("elapsed_ms", 0)) for row in frames), default=0),
        "callback_gap_p95_ms": percentile(callback_gaps, 0.95), "callback_gap_max_ms": max(callback_gaps, default=None),
        "draw_p95_ms": percentile([float(row["draw_duration_ms"]) for row in frames if finite(row.get("draw_duration_ms"))], 0.95),
        "callback_work_p95_ms": percentile([float(row["callback_work_ms"]) for row in frames if finite(row.get("callback_work_ms"))], 0.95),
        "encode_latency_median_ms": statistics.median(encodes) if encodes else None,
        "encode_latency_p95_ms": percentile(encodes, 0.95), "encode_latency_max_ms": max(encodes, default=None),
        "initial_frame_encode_latency_ms": case.get("phase9_initial_frame_encode_latency_ms"),
        "max_pending_encodes": int(case.get("max_pending_encodes", 0)),
        "pending_encodes_after_flush": int(case.get("pending_encodes_after_flush", -1)),
        "video_ended_at_finish": bool(case.get("video_ended_at_finish")),
        "video_current_time_at_end": case.get("video_current_time_at_end"),
        "end_drain_grace_ms": case.get("phase9_end_drain_grace_ms"),
        "browser_dropped_video_frames": case.get("browser_dropped_video_frames"),
        "visible_frame_pct": 100 * sum(row.get("visibility_state") == "visible" for row in frames) / max(1, len(frames)),
        "focused_frame_pct": 100 * sum(row.get("has_focus") is True for row in frames) / max(1, len(frames)),
        "source_video_sha256": pts_meta["source_video_sha256"], "capture_sha256": case.get("capture_sha256"),
    }
    events = [{"encoder": encoder, "repeat": repeat, "video_path": case["video_path"], **event} for event in case.get("timeline_events", [])]
    heartbeats = [{"encoder": encoder, "repeat": repeat, "video_path": case["video_path"], **event} for event in case.get("heartbeat_samples", [])]
    return summary, frame_rows, events, heartbeats


def run(args: argparse.Namespace) -> dict[str, Any]:
    official = read_and_verify_manifest(args.manifest.resolve(), args.repo.resolve(), (ROOT / "config" / "label_map_include50.json").resolve())
    official_map = {normalize_video_path(row["video_path"]).casefold(): row for row in official}
    target_rows = [official_map[path.casefold()] for path in TARGET_PATHS]
    run_dirs: dict[tuple[str, int], Path] = {}
    for encoder in ENCODERS:
        for repeat in REPEATS:
            run_dirs[(encoder, repeat)] = getattr(args, f"{encoder}_r{repeat}_dir").resolve()
    pts_by_path: dict[str, list[float]] = {}
    pts_meta_by_path: dict[str, dict[str, Any]] = {}
    for row in target_rows:
        source = locate_source(args.data_root.resolve(), row["video_path"])
        pts, meta = ffprobe_pts(args.ffprobe.resolve(), source)
        pts_by_path[row["video_path"]] = pts
        pts_meta_by_path[row["video_path"]] = {"video_path": row["video_path"], **meta}

    clip_rows: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    heartbeat_rows: list[dict[str, Any]] = []
    capture_hashes: dict[str, str] = {}
    source_sets: dict[tuple[str, int, str], set[int]] = {}
    source_image_hashes: dict[tuple[str, int, str], dict[int, str]] = {}
    for encoder in ENCODERS:
        for repeat in REPEATS:
            run_id = f"{encoder}_r{repeat}"
            run_dir = run_dirs[(encoder, repeat)]
            index = json.loads((run_dir / "capture_index.json").read_text(encoding="utf-8"))
            for row in target_rows:
                key = normalize_video_path(row["video_path"]).casefold()
                record_map = {normalize_video_path(item["video_path"]).casefold(): item for item in index.get("received", [])}
                if key not in record_map:
                    raise FileNotFoundError(f"{run_id}: missing {row['video_path']}")
                record = record_map[key]
                loaded = read_capture(run_dir, row, encoder, repeat, run_id)
                pts = pts_by_path[row["video_path"]]
                pts_meta = pts_meta_by_path[row["video_path"]]
                summary, frames, events, heartbeats = analyze_capture(encoder, repeat, loaded["case"], pts, pts_meta, loaded["frames"])
                summary["capture_sha256"] = loaded["sha256"]
                clip_rows.append(summary)
                frame_rows.extend([{**item, "capture_sha256": loaded["sha256"]} for item in frames])
                event_rows.extend(events)
                heartbeat_rows.extend(heartbeats)
                source_sets[(encoder, repeat, row["video_path"])] = {item["source_pts_frame_index"] for item in frames}
                source_image_hashes[(encoder, repeat, row["video_path"])] = {item["source_pts_frame_index"]: item["jpeg_sha256"] for item in frames}
                capture_hashes[f"{run_id}/{Path(record['container']).name}"] = loaded["sha256"]

    repeatability_rows: list[dict[str, Any]] = []
    for encoder in ENCODERS:
        for row in target_rows:
            path = row["video_path"]
            sets = [source_sets[(encoder, repeat, path)] for repeat in REPEATS]
            pair_scores = [jaccard(a, b) for a, b in combinations(sets, 2)]
            hash_maps = [source_image_hashes[(encoder, repeat, path)] for repeat in REPEATS]
            image_pair_scores = []
            image_pair_common_counts = []
            for left, right in combinations(hash_maps, 2):
                common = set(left) & set(right)
                image_pair_common_counts.append(len(common))
                image_pair_scores.append(100 * sum(left[index] == right[index] for index in common) / max(1, len(common)))
            summaries = [next(item for item in clip_rows if item["encoder"] == encoder and item["repeat"] == repeat and item["video_path"] == path) for repeat in REPEATS]
            coverages = [float(item["source_frame_coverage_pct"]) for item in summaries]
            repeatability_rows.append({
                "encoder": encoder, "video_path": path, "label": row["label"],
                "repeat1_coverage_pct": coverages[0], "repeat2_coverage_pct": coverages[1], "repeat3_coverage_pct": coverages[2],
                "mean_coverage_pct": statistics.mean(coverages), "min_coverage_pct": min(coverages), "max_coverage_pct": max(coverages),
                "coverage_spread_pp": max(coverages) - min(coverages),
                "all_repeats_initial_frame_verified": all(item["initial_paused_frame_count"] == 1 and item["first_record_is_initial_paused"] and item["initial_frame_timestamp_zero"] and item["initial_frame_video_paused_at_zero"] and item["initial_frame_event_precedes_play"] and item["initial_frame_maps_to_source_index_zero"] for item in summaries),
                "all_repeats_full_coverage": all(item["source_frame_coverage_pct"] == 100.0 for item in summaries),
                "exact_source_index_sets_identical": sets[0] == sets[1] == sets[2],
                "mean_pairwise_source_index_jaccard": statistics.mean(pair_scores),
                "mean_pairwise_identical_jpeg_sha256_pct": statistics.mean(image_pair_scores) if image_pair_scores else None,
                "min_pairwise_common_jpeg_frames": min(image_pair_common_counts) if image_pair_common_counts else 0,
                "all_common_source_frames_have_identical_jpeg_sha256": bool(image_pair_scores) and all(score == 100.0 for score in image_pair_scores),
                "all_repeats_no_duplicate_source_matches": all(item["duplicate_source_frame_matches"] == 0 for item in summaries),
            })

    encoder_rows: list[dict[str, Any]] = []
    for encoder in ENCODERS:
        subset = [row for row in clip_rows if row["encoder"] == encoder]
        total_unique = sum(int(row["unique_source_frames_captured"]) for row in subset)
        total_source = sum(int(row["source_frame_count"]) for row in subset)
        repeatability = [row for row in repeatability_rows if row["encoder"] == encoder]
        all_run_rows = [row for row in subset]
        encoder_rows.append({
            "encoder": encoder, "clip_repeat_runs": len(subset), "captured_unique_source_frames": total_unique,
            "source_frame_opportunities": total_source, "weighted_source_frame_coverage_pct": 100 * total_unique / max(1, total_source),
            "mean_clip_repeat_coverage_pct": statistics.mean(float(row["source_frame_coverage_pct"]) for row in subset),
            "full_coverage_clip_repeats": sum(row["source_frame_coverage_pct"] == 100.0 for row in subset),
            "initial_frame_verified_clip_repeats": sum(row["initial_paused_frame_count"] == 1 and row["first_record_is_initial_paused"] and row["initial_frame_timestamp_zero"] and row["initial_frame_video_paused_at_zero"] and row["initial_frame_event_precedes_play"] and row["initial_frame_maps_to_source_index_zero"] for row in subset),
            "duplicate_source_match_total": sum(int(row["duplicate_source_frame_matches"]) for row in subset),
            "max_pending_encodes_across_runs": max(int(row["max_pending_encodes"]) for row in subset),
            "max_pending_encodes_after_flush": max(int(row["pending_encodes_after_flush"]) for row in subset),
            "mean_encode_latency_p95_ms": statistics.mean(float(row["encode_latency_p95_ms"]) for row in subset if row["encode_latency_p95_ms"] is not None),
            "mean_pairwise_repeatability_jaccard": statistics.mean(float(row["mean_pairwise_source_index_jaccard"]) for row in repeatability),
            "clips_with_identical_source_sets_all_repeats": sum(bool(row["exact_source_index_sets_identical"]) for row in repeatability),
        })

    encoder_comparison: list[dict[str, Any]] = []
    for row in target_rows:
        path = row["video_path"]
        for repeat in REPEATS:
            sync = next(item for item in clip_rows if item["encoder"] == "sync" and item["repeat"] == repeat and item["video_path"] == path)
            async_ = next(item for item in clip_rows if item["encoder"] == "async" and item["repeat"] == repeat and item["video_path"] == path)
            encoder_comparison.append({
                "video_path": path, "label": row["label"], "repeat": repeat,
                "sync_coverage_pct": sync["source_frame_coverage_pct"], "async_coverage_pct": async_["source_frame_coverage_pct"],
                "async_minus_sync_coverage_pp": async_["source_frame_coverage_pct"] - sync["source_frame_coverage_pct"],
                "sync_encode_p95_ms": sync["encode_latency_p95_ms"], "async_encode_p95_ms": async_["encode_latency_p95_ms"],
                "sync_max_pending": sync["max_pending_encodes"], "async_max_pending": async_["max_pending_encodes"],
            })

    first_frame_checks = sum(1 for row in clip_rows if row["initial_paused_frame_count"] == 1 and row["first_record_is_initial_paused"] and row["initial_frame_timestamp_zero"] and row["initial_frame_video_paused_at_zero"] and row["initial_frame_event_precedes_play"] and row["initial_frame_maps_to_source_index_zero"])
    full_coverage_runs = sum(1 for row in clip_rows if row["source_frame_coverage_pct"] == 100.0)
    zero_duplicate_runs = sum(1 for row in clip_rows if row["duplicate_source_frame_matches"] == 0)
    no_interior_gaps_runs = sum(1 for row in clip_rows if not any(a > 0 and b < row["source_frame_count"] - 1 for a, b in row["missing_source_frame_ranges"]))
    queues_drained = sum(1 for row in clip_rows if row["pending_encodes_after_flush"] == 0)
    queue_bounded = all(row["max_pending_encodes"] <= 4 for row in clip_rows)
    repeat_exact = sum(1 for row in repeatability_rows if row["exact_source_index_sets_identical"])
    repeat_full = sum(1 for row in repeatability_rows if row["all_repeats_full_coverage"])

    summary = {
        "status": "completed", "phase": 9, "repeats_per_encoder": 3, "encoders": list(ENCODERS),
        "official_validation_manifest_count": len(official), "diagnostic_clip_count": len(target_rows),
        "total_clip_repeat_runs": len(clip_rows), "total_source_frame_opportunities": sum(int(row["source_frame_count"]) for row in clip_rows),
        "initial_paused_frame_policy": "draw and encode at currentTime=0 before calling video.play()",
        "callback_gate_ms": 0, "capture_timestamp": "requestVideoFrameCallback.metadata.mediaTime for playback frames; explicit currentTime=0 for initial paused frame",
        "classifier_checkpoint_loaded": False, "iot_enabled": False, "production_code_modified": False,
        "acceptance_checks": {
            "initial_frame_verified_runs": f"{first_frame_checks}/{len(clip_rows)}",
            "full_coverage_clip_repeat_runs": f"{full_coverage_runs}/{len(clip_rows)}",
            "no_duplicate_source_matches_runs": f"{zero_duplicate_runs}/{len(clip_rows)}",
            "no_interior_source_gaps_runs": f"{no_interior_gaps_runs}/{len(clip_rows)}",
            "encoding_queues_drained_runs": f"{queues_drained}/{len(clip_rows)}",
            "max_pending_queue_at_most_four": queue_bounded,
            "repeat_source_sets_identical": f"{repeat_exact}/{len(repeatability_rows)} clip-encoder groups",
            "all_three_repeats_full_coverage": f"{repeat_full}/{len(repeatability_rows)} clip-encoder groups",
        },
        "encoder_summary": encoder_rows, "clip_repeat_metrics": clip_rows,
        "repeatability": repeatability_rows, "encoder_comparisons": encoder_comparison,
        "source_videos": list(pts_meta_by_path.values()),
        "interpretation_note": "A paused initial-frame snapshot is mapped to the nearest FFprobe best_effort_timestamp_time; playback frames use requestVideoFrameCallback metadata.mediaTime. getVideoPlaybackQuality counters are not subtracted from presentedFrames. Three repeats are within-session repeatability evidence, not cross-device generalization.",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase9_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(args.output_dir / "phase9_clip_repeat_metrics.csv", clip_rows)
    write_csv(args.output_dir / "phase9_frame_metrics.csv", frame_rows)
    write_csv(args.output_dir / "phase9_repeatability.csv", repeatability_rows)
    write_csv(args.output_dir / "phase9_encoder_summary.csv", encoder_rows)
    write_csv(args.output_dir / "phase9_encoder_comparison.csv", encoder_comparison)
    write_csv(args.output_dir / "phase9_event_timeline.csv", event_rows)
    write_csv(args.output_dir / "phase9_playback_heartbeats.csv", heartbeat_rows)
    write_csv(args.output_dir / "phase9_source_pts_metadata.csv", [{"video_path": path, **meta} for path, meta in pts_meta_by_path.items()])

    report = [
        "# Phase 9 — first-frame recovery and repeatability report", "",
        "## Scope and guardrails", "",
        "Two ungated capture encoders were compared: synchronous toDataURL and asynchronous toBlob. Each encoder was repeated three times across the same six diagnostic clips (36 clip-repeat runs).",
        "For every clip, the initial paused frame was drawn and encoded at currentTime=0 before video.play() was invoked. Playback frames were then captured through requestVideoFrameCallback without an acceptance gate.",
        "Canvas width was 640 px and JPEG quality 0.92. Source-frame identity uses FFprobe best_effort_timestamp_time. No classifier/checkpoint, production recognition path or IoT endpoint was used.", "",
        "## Encoder-level summary", "",
        "| Encoder | Clip-repeat runs | Unique source frames captured | Source opportunities | Coverage | Full-coverage runs | Initial frame verified | Max pending |", "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in encoder_rows:
        report.append(f"| {item['encoder']} | {item['clip_repeat_runs']} | {item['captured_unique_source_frames']} | {item['source_frame_opportunities']} | {item['weighted_source_frame_coverage_pct']:.2f}% | {item['full_coverage_clip_repeats']} | {item['initial_frame_verified_clip_repeats']}/{len(clip_rows)//2} | {item['max_pending_encodes_across_runs']} |")
    report += ["", "## Repeatability by clip and encoder", "", "| Encoder | Clip | Repeat 1 | Repeat 2 | Repeat 3 | Mean | Spread | Same source-index sets | Pairwise index Jaccard | Identical JPEG SHA-256 on common frames |", "|---|---|---:|---:|---:|---:|---:|---|---:|---:|"]
    for item in repeatability_rows:
        image_pct = item["mean_pairwise_identical_jpeg_sha256_pct"]
        image_text = "n/a" if image_pct is None else f"{image_pct:.1f}%"
        report.append(f"| {item['encoder']} | {Path(item['video_path']).stem} | {item['repeat1_coverage_pct']:.1f}% | {item['repeat2_coverage_pct']:.1f}% | {item['repeat3_coverage_pct']:.1f}% | {item['mean_coverage_pct']:.1f}% | {item['coverage_spread_pp']:.1f} pp | {'yes' if item['exact_source_index_sets_identical'] else 'no'} | {item['mean_pairwise_source_index_jaccard']:.3f} | {image_text} |")
    report += ["", "## Acceptance checks", "", "| Check | Result |", "|---|---|"]
    for key, value in summary["acceptance_checks"].items():
        report.append(f"| {key.replace('_', ' ')} | {value} |")
    report += ["", "## Interpretation limits", "",
               "Three runs per encoder on one browser/device provide within-session repeatability evidence only. They do not establish cross-device repeatability or production-wide behaviour.",
               "The initial paused snapshot uses currentTime=0 after loadeddata/seeked and is explicitly flagged in the frame metadata. It is not a requestVideoFrameCallback event; playback frames use metadata.mediaTime.",
               "The capture receiver verifies the pinned official 77-item validation manifest, but this experiment only captures six diagnostic clips.", "",
               "## Engineering decision", "",
               "This experiment is diagnostic-only. Do not modify production capture, model inputs, checkpoint, class order, confidence policy or IoT configuration solely on this result. Promote a capture mode only if first-frame capture, full source coverage, zero duplicate source matches, repeatability and drained/bounded encoding queues are demonstrated."]
    (args.output_dir / "PHASE9_REPEATABILITY_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    for encoder in ENCODERS:
        for repeat in REPEATS:
            parser.add_argument(f"--{encoder}-r{repeat}-dir", dest=f"{encoder}_r{repeat}_dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args)
    print("PHASE9_STATUS=completed")
    for item in summary["encoder_summary"]:
        print(f"ENCODER={item['encoder']} | coverage={item['weighted_source_frame_coverage_pct']:.2f}% | captured={item['captured_unique_source_frames']}/{item['source_frame_opportunities']} | full_runs={item['full_coverage_clip_repeat_runs'] if 'full_coverage_clip_repeat_runs' in item else item['full_coverage_clip_repeats']} | max_queue={item['max_pending_encodes_across_runs']}")
    print("INITIAL_FRAME_VERIFIED=" + summary["acceptance_checks"]["initial_frame_verified_runs"])
    print("REPEATS_IDENTICAL=" + summary["acceptance_checks"]["repeat_source_sets_identical"])
    print(f"OUTPUT={args.output_dir.resolve()}")
    print("IoT disabled; classifier/checkpoint not loaded; production code unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
