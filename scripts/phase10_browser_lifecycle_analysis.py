#!/usr/bin/env python3
"""Analyze Phase 10 browser focus, visibility, lifecycle and callback continuity."""
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
from collections import Counter, defaultdict
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
from phase9_repeatability_analysis import ffprobe_pts, locate_source, nearest_index, missing_ranges, percentile  # noqa: E402

TARGET_PATHS = (
    "Greetings/51. Good Morning/MVI_0099.MOV",
    "Colours/55. White/MVI_5063.MOV",
    "Adjectives/83. big large/MVI_9396.MOV",
    "Adjectives/79. short/MVI_9302.MOV",
    "Means_of_Transportation/11. Car/MVI_3118.MOV",
    "Pronouns/40. I/MVI_9922.MOV",
)
MODES = ("foreground_focused", "visible_unfocused", "hidden_background", "focus_recovery")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def safe_capture_path(run_dir: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe capture path in index: {relative}")
    resolved = (run_dir / rel).resolve()
    resolved.relative_to(run_dir.resolve())
    return resolved


def focus_state_counts(frames: list[dict[str, Any]]) -> dict[str, int]:
    callbacks = [f for f in frames if f.get("capture_kind") == "rvfc_playback"]
    visible_focused = sum(f.get("visibility_state") == "visible" and f.get("has_focus") is True for f in callbacks)
    visible_unfocused = sum(f.get("visibility_state") == "visible" and f.get("has_focus") is False for f in callbacks)
    hidden = sum(f.get("visibility_state") == "hidden" for f in callbacks)
    other = len(callbacks) - visible_focused - visible_unfocused - hidden
    return {
        "callback_frames": len(callbacks),
        "callbacks_visible_focused": visible_focused,
        "callbacks_visible_unfocused": visible_unfocused,
        "callbacks_hidden": hidden,
        "callbacks_other_or_unknown": other,
    }


def pair_event_durations(timeline: list[dict[str, Any]], start_type: str, end_type: str) -> list[float]:
    starts: list[float] = []
    durations: list[float] = []
    for event in sorted(timeline, key=lambda e: float(e.get("elapsed_ms", 0) or 0)):
        kind = event.get("event_type")
        elapsed = float(event.get("elapsed_ms", 0) or 0)
        if kind == start_type:
            starts.append(elapsed)
        elif kind == end_type and starts:
            start = starts.pop(0)
            if elapsed >= start:
                durations.append(elapsed - start)
    return durations




def visibility_hidden_durations(timeline: list[dict[str, Any]]) -> list[float]:
    """Pair visibilitychange transitions into hidden and back to visible."""
    hidden_start: float | None = None
    durations: list[float] = []
    for event in sorted(timeline, key=lambda e: float(e.get("elapsed_ms", 0) or 0)):
        if event.get("event_type") != "document_visibilitychange":
            continue
        state = event.get("visibility_state_after") or event.get("visibility_state")
        elapsed = float(event.get("elapsed_ms", 0) or 0)
        if state == "hidden" and hidden_start is None:
            hidden_start = elapsed
        elif state == "visible" and hidden_start is not None:
            if elapsed >= hidden_start:
                durations.append(elapsed - hidden_start)
            hidden_start = None
    return durations


def focus_recovery_latencies(timeline: list[dict[str, Any]]) -> list[float]:
    ordered = sorted(timeline, key=lambda e: float(e.get("elapsed_ms", 0) or 0))
    latencies: list[float] = []
    blur_pending = False
    for idx, event in enumerate(ordered):
        kind = event.get("event_type")
        if kind == "window_blur":
            blur_pending = True
            continue
        if kind != "window_focus" or not blur_pending:
            continue
        blur_pending = False
        start = float(event.get("elapsed_ms", 0) or 0)
        next_callback = next((float(item.get("elapsed_ms", 0) or 0) for item in ordered[idx + 1:]
                              if item.get("event_type") == "rvfc_delivered"), None)
        if next_callback is not None and next_callback >= start:
            latencies.append(next_callback - start)
    return latencies


def summarize_frames(pts: list[float], frames: list[dict[str, Any]]) -> dict[str, Any]:
    mappings = [nearest_index(pts, float(frame["video_time_seconds"])) for frame in frames]
    indexes = [idx for idx, _ in mappings]
    unique = set(indexes)
    duplicates = len(indexes) - len(unique)
    initial = [i for i, frame in enumerate(frames) if frame.get("capture_kind") == "initial_paused"]
    callback_frames = [f for f in frames if f.get("capture_kind") == "rvfc_playback"]
    presented = [int(f["presented_frames"]) for f in callback_frames if f.get("presented_frames") is not None]
    presented_gaps = [max(0, b - a - 1) for a, b in zip(presented, presented[1:])]
    mapped_by_capture = {i: idx for i, idx in enumerate(indexes)}
    missing = sorted(set(range(len(pts))) - unique)
    interior_missing = [i for i in missing if i not in {0, len(pts) - 1}]
    deltas_ms = [delta * 1000 for _, delta in mappings]
    callback_times = [float(f["callback_time_ms"]) for f in callback_frames if f.get("callback_time_ms") is not None]
    callback_gaps = [b - a for a, b in zip(callback_times, callback_times[1:]) if b >= a]
    return {
        "source_frame_count": len(pts),
        "captured_frame_count": len(frames),
        "unique_source_frames_captured": len(unique),
        "source_frame_coverage_pct": round(100 * len(unique) / max(1, len(pts)), 3),
        "duplicate_source_frame_matches": duplicates,
        "missing_source_frame_count": len(missing),
        "missing_interior_frame_count": len(interior_missing),
        "missing_source_frame_ranges": missing_ranges(unique, len(pts)),
        "initial_paused_records": len(initial),
        "first_record_is_initial_paused": bool(frames and frames[0].get("capture_kind") == "initial_paused"),
        "initial_frame_maps_to_source_zero": bool(initial and mapped_by_capture.get(initial[0]) == 0),
        "callback_frames": len(callback_frames),
        "presented_frame_gap_total": sum(presented_gaps),
        "presented_frame_gap_max": max(presented_gaps) if presented_gaps else 0,
        "callback_gap_p95_ms": percentile(callback_gaps, 0.95),
        "callback_gap_max_ms": max(callback_gaps) if callback_gaps else None,
        "pts_abs_delta_p95_ms": percentile([abs(v) for v in deltas_ms], 0.95),
        "pts_abs_delta_max_ms": max((abs(v) for v in deltas_ms), default=None),
        "mapped_source_indexes": indexes,
    }


def classify_mode_exposure(
    mode: str, *, event_types: list[str], focus_counts: dict[str, int], marker: Any,
    blur_visible_count: int = 0, hidden_transition_count: int = 0,
    initial_visibility: str | None = None, initial_focus: bool | None = None,
) -> tuple[bool, str]:
    blur_count = event_types.count("window_blur")
    focus_count = event_types.count("window_focus")
    if mode == "foreground_focused":
        ok = (initial_visibility == "visible" and initial_focus is True
              and focus_counts["callbacks_visible_focused"] > 0
              and focus_counts["callbacks_hidden"] == 0
              and focus_counts["callbacks_visible_unfocused"] == 0
              and blur_count == 0 and hidden_transition_count == 0)
        return ok, "focused-visible samples observed without blur/hidden events" if ok else "control condition not maintained for all observed callbacks"
    if mode == "visible_unfocused":
        initial_unfocused = initial_visibility == "visible" and initial_focus is False
        ok = blur_visible_count > 0 or initial_unfocused or focus_counts["callbacks_visible_unfocused"] > 0
        return ok, "visible-but-unfocused state observed" if ok else "no sample/event confirmed visible-but-unfocused exposure"
    if mode == "hidden_background":
        ok = hidden_transition_count > 0 or initial_visibility == "hidden" or focus_counts["callbacks_hidden"] > 0
        return ok, "document hidden state observed" if ok else "hidden/background exposure was not confirmed"
    # The controlled challenge is intentionally user-driven; require a blur/focus pair and a trusted marker.
    ok = blur_count > 0 and focus_count > 0 and isinstance(marker, dict) and marker.get("trusted") is True
    return ok, "blur/focus recovery and trusted interaction marker observed" if ok else "focus-recovery challenge was incomplete or unverified"


def load_capture(run_dir: Path, expected_row: dict[str, str], mode: str, run_id: str) -> tuple[dict[str, Any], tuple[bytes, ...]]:
    index_path = run_dir / "capture_index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"Capture index missing: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("official_split_count") != 77 or index.get("received_count") != 6:
        raise ValueError(f"{mode}: capture index must contain exactly six of the pinned 77 validation rows")
    cfg_index = index.get("capture_config", {})
    if (cfg_index.get("phase10_mode"), cfg_index.get("phase10_run_id")) != (mode, run_id):
        raise ValueError(f"{mode}: index lifecycle identity mismatch")
    wanted = normalize_video_path(expected_row["video_path"]).casefold()
    record = next((item for item in index.get("received", []) if normalize_video_path(item["video_path"]).casefold() == wanted), None)
    if record is None:
        raise FileNotFoundError(f"{mode}: missing captured clip {expected_row['video_path']}")
    container = safe_capture_path(run_dir, record.get("container", ""))
    payload = container.read_bytes()
    parsed = parse_capture_payload(payload, expected_row)
    if parsed.sha256 != record.get("sha256"):
        raise ValueError(f"{mode}: container SHA-256 mismatch for {expected_row['video_path']}")
    case = parsed.header["case"]
    config = parsed.header["capture_config"]
    if (case.get("phase10_mode"), case.get("phase10_run_id"), case.get("phase10_capture_encoder")) != (mode, run_id, "sync"):
        raise ValueError(f"{mode}: case identity mismatch")
    if (config.get("phase10_mode"), config.get("phase10_run_id"), config.get("phase10_capture_encoder")) != (mode, run_id, "sync"):
        raise ValueError(f"{mode}: payload config identity mismatch")
    return case, parsed.frames


def analyze_one(mode: str, run_id: str, case: dict[str, Any], image_payloads: tuple[bytes, ...], pts: list[float], pts_meta: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    frames = case["frames"]
    frame_summary = summarize_frames(pts, frames)
    timeline = case.get("timeline_events", [])
    heartbeats = case.get("heartbeat_samples", [])
    event_types = [str(event.get("event_type", "")) for event in timeline]
    focus_counts = focus_state_counts(frames)
    marker = case.get("focus_recovery_marker")
    initial_frame = frames[0] if frames else {}
    blur_visible_count = sum(1 for event in timeline if event.get("event_type") == "window_blur" and event.get("visibility_state") == "visible" and event.get("has_focus") is False)
    hidden_transition_count = sum(1 for event in timeline if event.get("event_type") == "document_visibilitychange" and (event.get("visibility_state_after") == "hidden" or event.get("visibility_state") == "hidden"))
    exposure_ok, exposure_note = classify_mode_exposure(
        mode, event_types=event_types, focus_counts=focus_counts, marker=marker,
        blur_visible_count=blur_visible_count, hidden_transition_count=hidden_transition_count,
        initial_visibility=initial_frame.get("visibility_state"), initial_focus=initial_frame.get("has_focus"),
    )
    focus_pairs = pair_event_durations(timeline, "window_blur", "window_focus")
    hidden_durations = visibility_hidden_durations(timeline)
    recovery_latencies = focus_recovery_latencies(timeline)
    heartbeat_times = [float(item["elapsed_ms"]) for item in heartbeats if item.get("elapsed_ms") is not None]
    heartbeat_gaps = [b - a for a, b in zip(heartbeat_times, heartbeat_times[1:]) if b >= a]
    playback_events = Counter(event for event in event_types if event.startswith("video_"))
    row = {
        "mode": mode, "run_id": run_id, "video_path": case["video_path"], "label": case["label"],
        **{k: v for k, v in frame_summary.items() if k != "mapped_source_indexes"},
        **focus_counts,
        "window_blur_events": event_types.count("window_blur"),
        "window_blur_while_visible_events": blur_visible_count,
        "window_focus_events": event_types.count("window_focus"),
        "hidden_visibility_transitions": hidden_transition_count,
        "initial_frame_visibility": initial_frame.get("visibility_state"),
        "initial_frame_has_focus": initial_frame.get("has_focus"),
        "visibility_change_events": event_types.count("document_visibilitychange"),
        "hidden_visibility_transitions": hidden_transition_count,
        "hidden_duration_median_ms": statistics.median(hidden_durations) if hidden_durations else None,
        "hidden_duration_max_ms": max(hidden_durations) if hidden_durations else None,
        "pagehide_events": event_types.count("pagehide"), "pageshow_events": event_types.count("pageshow"),
        "page_freeze_events": event_types.count("page_freeze"), "page_resume_events": event_types.count("page_resume"),
        "rvfc_silence_events": event_types.count("rvfc_silence_detected"),
        "focus_blur_duration_median_ms": statistics.median(focus_pairs) if focus_pairs else None,
        "focus_blur_duration_max_ms": max(focus_pairs) if focus_pairs else None,
        "focus_to_next_callback_median_ms": statistics.median(recovery_latencies) if recovery_latencies else None,
        "focus_to_next_callback_max_ms": max(recovery_latencies) if recovery_latencies else None,
        "heartbeat_samples": len(heartbeats), "heartbeat_gap_p95_ms": percentile(heartbeat_gaps, 0.95),
        "heartbeat_gap_max_ms": max(heartbeat_gaps) if heartbeat_gaps else None,
        "browser_dropped_video_frames": int(case.get("browser_dropped_video_frames", 0) or 0),
        "browser_total_video_frames": int(case.get("browser_total_video_frames", 0) or 0),
        "video_waiting_events": playback_events.get("video_waiting", 0),
        "video_stalled_events": playback_events.get("video_stalled", 0),
        "video_playing_events": playback_events.get("video_playing", 0),
        "video_ended_events": playback_events.get("video_ended", 0),
        "video_ended_at_finish": bool(case.get("video_ended_at_finish")),
        "video_paused_at_finish": bool(case.get("video_paused_at_finish")),
        "page_visibility_at_finish": case.get("page_visibility_at_finish"),
        "page_has_focus_at_finish": case.get("page_has_focus_at_finish"),
        "page_was_discarded": bool(case.get("page_was_discarded")),
        "recovery_marker_trusted": bool(isinstance(marker, dict) and marker.get("trusted") is True),
        "recovery_marker_count": int(isinstance(marker, dict)),
        "mode_exposure_confirmed": exposure_ok, "mode_exposure_note": exposure_note,
        "source_pts_median_step_ms": pts_meta["pts_step_median_ms"],
        "source_video_sha256": pts_meta["source_video_sha256"],
    }
    frame_rows: list[dict[str, Any]] = []
    for capture_idx, (frame, jpeg) in enumerate(zip(frames, image_payloads)):
        source_idx, delta = nearest_index(pts, float(frame["video_time_seconds"]))
        frame_rows.append({
            "mode": mode, "run_id": run_id, "video_path": case["video_path"], "label": case["label"],
            "capture_frame_index": capture_idx, "capture_kind": frame.get("capture_kind"),
            "source_pts_frame_index": source_idx, "source_pts_seconds": pts[source_idx],
            "capture_minus_pts_ms": round(delta * 1000, 6),
            "presented_frames": frame.get("presented_frames"), "callback_time_ms": frame.get("callback_time_ms"),
            "elapsed_ms": frame.get("elapsed_ms"), "visibility_state": frame.get("visibility_state"),
            "has_focus": frame.get("has_focus"), "video_ready_state": frame.get("video_ready_state"),
            "callback_work_ms": frame.get("callback_work_ms"), "draw_duration_ms": frame.get("draw_duration_ms"),
            "encode_latency_ms": frame.get("encode_latency_ms"), "jpeg_bytes": len(jpeg),
            "jpeg_sha256": hashlib.sha256(jpeg).hexdigest(),
        })
    timeline_rows = [{"mode": mode, "run_id": run_id, "video_path": case["video_path"], **event} for event in timeline]
    heartbeat_rows = [{"mode": mode, "run_id": run_id, "video_path": case["video_path"], **sample} for sample in heartbeats]
    return row, frame_rows, timeline_rows, heartbeat_rows


def build_report(mode_rows: list[dict[str, Any]], clip_rows: list[dict[str, Any]], summary: dict[str, Any], pts_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 10 — Browser lifecycle and focus diagnostics", "",
        "## Executive summary", "",
        f"- Status: **{summary['status']}**; lifecycle modes completed: **{summary['mode_count']}/4**; clips: **{summary['clip_capture_count']}/24**.",
        f"- Total source-frame opportunities: **{summary['source_frame_opportunities']}**; unique source frames captured: **{summary['unique_source_frames_captured']}**; weighted coverage: **{summary['weighted_source_coverage_pct']:.2f}%**.",
        "- Encoder, Canvas size, JPEG quality, callback gate and first-paused-frame policy were held constant. Only browser lifecycle condition changed.",
        "- Classifier/checkpoint was not loaded; IoT remained disabled; production code was not changed.", "",
        "## Mode comparison", "",
        "| Mode | Coverage | Blur events | Hidden transitions | Max hidden duration | Hidden callbacks | Visible-unfocused callbacks | Callback blackout events | Focus-recovery markers | Exposure confirmed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in mode_rows:
        hidden_ms = row['hidden_duration_max_ms'] if row['hidden_duration_max_ms'] is not None else 'NA'
        lines.append(f"| {row['mode']} | {row['source_frame_coverage_pct']:.2f}% | {row['window_blur_events']} | {row['hidden_visibility_transitions']} | {hidden_ms} ms | {row['callbacks_hidden']} | {row['callbacks_visible_unfocused']} | {row['rvfc_silence_events']} | {row['recovery_marker_count']} | {'Yes' if row['mode_exposure_confirmed'] else 'No'} |")
    lines += ["", "## Per-clip comparison", "", "| Mode | Clip | Coverage | Missing interior frames | Browser dropped frames | Max callback gap | Max heartbeat gap | Ended | Exposure |", "|---|---|---:|---:|---:|---:|---:|---|---|"]
    for row in clip_rows:
        lines.append(f"| {row['mode']} | {row['label']} | {row['source_frame_coverage_pct']:.1f}% | {row['missing_interior_frame_count']} | {row['browser_dropped_video_frames']} | {row['callback_gap_max_ms'] if row['callback_gap_max_ms'] is not None else 'NA'} ms | {row['heartbeat_gap_max_ms'] if row['heartbeat_gap_max_ms'] is not None else 'NA'} ms | {'Yes' if row['video_ended_at_finish'] else 'No'} | {'Yes' if row['mode_exposure_confirmed'] else 'No'} |")
    lines += ["", "## Interpretation guidance", "",
              "- `foreground_focused` is a valid control only when the captured callback samples are visible and focused and the run records no blur/hidden transition.",
              "- `visible_unfocused` exposure is confirmed by a blur event and at least one callback sample with `visibilityState=visible` and `document.hasFocus()=false`.",
              "- `hidden_background` exposure is confirmed by a hidden visibility transition. Callback delivery may continue or pause; that is the outcome, not a test failure.",
              "- `focus_recovery` requires a blur/focus pair and a trusted user interaction marker. Callback recovery latency is measured from a focus event to the next delivered rVFC callback.",
              "- Browser dropped-frame counters, rVFC `presentedFrames`, mediaTime gaps, callback blackouts and throttled heartbeat gaps are separate observations and must not be treated as interchangeable.",
              "- This is a single within-device diagnostic pass, not cross-device generalization or production validation.", "",
              "## FFprobe provenance", "", "| Clip | Source frames | Median PTS step | Source SHA-256 |", "|---|---:|---:|---|"]
    for row in pts_rows:
        lines.append(f"| {row['video_path']} | {row['source_frame_count']} | {row['pts_step_median_ms']:.3f} ms | `{row['source_video_sha256']}` |")
    lines += ["", "## Guardrails", "", "No model inference, checkpoint loading, IoT/MQTT action or production recognition change occurred.", ""]
    return "\n".join(lines)


def analyze(args: argparse.Namespace) -> int:
    repo = args.repo.resolve(); data_root = args.data_root.resolve(); manifest = args.manifest.resolve(); ffprobe = args.ffprobe.resolve()
    rows = read_and_verify_manifest(manifest, repo, ROOT / "config" / "label_map_include50.json")
    by_path = {normalize_video_path(row["video_path"]).casefold(): row for row in rows}
    if len(rows) != 77:
        raise ValueError(f"Pinned validation manifest must contain 77 rows; found {len(rows)}")
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    mode_dirs = {mode: getattr(args, mode + "_dir").resolve() for mode in MODES}
    cache: dict[str, tuple[list[float], dict[str, Any], Path]] = {}
    clip_rows: list[dict[str, Any]] = []; frame_rows: list[dict[str, Any]] = []; timeline_rows: list[dict[str, Any]] = []; heartbeat_rows: list[dict[str, Any]] = []; pts_rows: list[dict[str, Any]] = []
    for mode in MODES:
        run_dir = mode_dirs[mode]
        index = json.loads((run_dir / "capture_index.json").read_text(encoding="utf-8"))
        if index.get("received_count") != 6:
            raise ValueError(f"{mode}: expected six received captures")
        run_id = mode
        for video_path in TARGET_PATHS:
            key = normalize_video_path(video_path).casefold()
            expected = by_path.get(key)
            if expected is None:
                raise ValueError(f"Target clip missing from official manifest: {video_path}")
            case, images = load_capture(run_dir, expected, mode, run_id)
            if key not in cache:
                source = locate_source(data_root, video_path)
                pts, meta = ffprobe_pts(ffprobe, source)
                cache[key] = (pts, meta, source)
                pts_rows.append({"video_path": video_path, "source_path": str(source), **meta})
            pts, pts_meta, _source = cache[key]
            row, fr, tl, hb = analyze_one(mode, run_id, case, images, pts, pts_meta)
            clip_rows.append(row); frame_rows.extend(fr); timeline_rows.extend(tl); heartbeat_rows.extend(hb)
    mode_rows: list[dict[str, Any]] = []
    for mode in MODES:
        group = [row for row in clip_rows if row["mode"] == mode]
        source_total = sum(int(row["source_frame_count"]) for row in group)
        unique_total = sum(int(row["unique_source_frames_captured"]) for row in group)
        summary = {
            "mode": mode,
            "source_frame_opportunities": source_total,
            "unique_source_frames_captured": unique_total,
            "source_frame_coverage_pct": round(100 * unique_total / max(1, source_total), 3),
            "window_blur_events": sum(row["window_blur_events"] for row in group),
            "window_focus_events": sum(row["window_focus_events"] for row in group),
            "visibility_change_events": sum(row["visibility_change_events"] for row in group),
            "hidden_visibility_transitions": sum(row["hidden_visibility_transitions"] for row in group),
            "hidden_duration_max_ms": max((row["hidden_duration_max_ms"] or 0 for row in group), default=0),
            "callbacks_hidden": sum(row["callbacks_hidden"] for row in group),
            "callbacks_visible_unfocused": sum(row["callbacks_visible_unfocused"] for row in group),
            "callbacks_visible_focused": sum(row["callbacks_visible_focused"] for row in group),
            "rvfc_silence_events": sum(row["rvfc_silence_events"] for row in group),
            "recovery_marker_count": sum(row["recovery_marker_count"] for row in group),
            "mode_exposure_confirmed": all(row["mode_exposure_confirmed"] for row in group),
            "clips_exposure_confirmed": sum(bool(row["mode_exposure_confirmed"]) for row in group),
            "browser_dropped_video_frames": sum(row["browser_dropped_video_frames"] for row in group),
            "callback_gap_max_ms": max((row["callback_gap_max_ms"] or 0 for row in group), default=0),
            "heartbeat_gap_max_ms": max((row["heartbeat_gap_max_ms"] or 0 for row in group), default=0),
        }
        mode_rows.append(summary)
    total_source = sum(row["source_frame_count"] for row in clip_rows)
    total_unique = sum(row["unique_source_frames_captured"] for row in clip_rows)
    overall = {
        "status": "completed", "mode_count": len(MODES), "clip_capture_count": len(clip_rows),
        "source_frame_opportunities": total_source, "unique_source_frames_captured": total_unique,
        "weighted_source_coverage_pct": round(100 * total_unique / max(1, total_source), 3),
        "ffprobe_source": "ffprobe_best_effort_timestamp_time", "ffprobe_clip_count": len(pts_rows),
        "iot_enabled": False, "classifier_checkpoint_loaded": False, "production_code_modified": False,
        "modes": mode_rows,
    }
    (output / "phase10_summary.json").write_text(json.dumps(overall, indent=2), encoding="utf-8")
    write_csv(output / "phase10_mode_metrics.csv", mode_rows)
    write_csv(output / "phase10_clip_metrics.csv", clip_rows)
    write_csv(output / "phase10_frame_metrics.csv", frame_rows)
    write_csv(output / "phase10_event_timeline.csv", timeline_rows)
    write_csv(output / "phase10_playback_heartbeats.csv", heartbeat_rows)
    write_csv(output / "phase10_source_pts_metadata.csv", pts_rows)
    (output / "PHASE10_BROWSER_LIFECYCLE_REPORT.md").write_text(build_report(mode_rows, clip_rows, overall, pts_rows), encoding="utf-8")
    print(f"PHASE10_STATUS=completed | modes={len(MODES)} | clips={len(clip_rows)} | weighted_coverage={overall['weighted_source_coverage_pct']:.2f}%", flush=True)
    for row in mode_rows:
        print(f"MODE={row['mode']} | coverage={row['source_frame_coverage_pct']:.2f}% | blur={row['window_blur_events']} | hidden_transitions={row['visibility_change_events']} | hidden_callbacks={row['callbacks_hidden']} | visible_unfocused_callbacks={row['callbacks_visible_unfocused']} | exposure_confirmed={row['mode_exposure_confirmed']}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    for mode in MODES:
        parser.add_argument(f"--{mode.replace('_', '-')}-dir", dest=f"{mode}_dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        return analyze(args)
    except Exception as exc:
        print(f"PHASE10_ANALYSIS_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
