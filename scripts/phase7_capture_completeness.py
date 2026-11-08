#!/usr/bin/env python3
"""Compare the current 40 ms Canvas policy with every presented-frame capture.

This is a read-only diagnostic. It consumes the two Phase 7 .islbrw capture
sets, verifies their pinned manifest identities, and aligns their mediaTime
values to FFprobe best_effort_timestamp_time values from the source videos.
No model, checkpoint, classifier, IoT or production application code is loaded.
"""
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
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import parse_capture_payload, normalize_video_path  # noqa: E402
from serve_browser_validation_capture import read_and_verify_manifest, safe_capture_id  # noqa: E402

TARGET_PATHS = [
    "Greetings/51. Good Morning/MVI_0099.MOV",
    "Colours/55. White/MVI_5063.MOV",
    "Adjectives/83. big large/MVI_9396.MOV",
    "Adjectives/79. short/MVI_9302.MOV",
    "Means_of_Transportation/11. Car/MVI_3118.MOV",
    "Pronouns/40. I/MVI_9922.MOV",
]
MODES = ("current_40ms", "every_presented_frame")


def percentile(values: Iterable[float], p: float) -> float | None:
    values = sorted(float(v) for v in values if _finite(v))
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(p * len(values)) - 1))
    return values[index]


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def sequence_gap_metrics(numbers: Iterable[int], total_presented: int | None = None) -> dict[str, int]:
    """Return interior sequence gaps and total unseen presented frames.

    presentedFrames is a monotonically increasing browser counter. Its exact
    starting value can be non-one, so the total unseen metric is computed
    against browser_total_video_frames when supplied.
    """
    seq = [int(x) for x in numbers]
    if any(b <= a for a, b in zip(seq, seq[1:])):
        raise ValueError("presentedFrames must be strictly increasing")
    interior = sum(max(0, b - a - 1) for a, b in zip(seq, seq[1:]))
    start_gap = max(0, seq[0] - 1) if seq else 0
    tail_gap = max(0, int(total_presented) - seq[-1]) if seq and total_presented is not None else 0
    all_gap = max(0, int(total_presented) - len(set(seq))) if total_presented is not None else start_gap + interior + tail_gap
    return {
        "interior_presented_frame_gaps": interior,
        "initial_presented_frame_gap": start_gap,
        "trailing_presented_frame_gap": tail_gap,
        "unseen_presented_frames": all_gap,
        "max_interior_gap": max((b - a - 1 for a, b in zip(seq, seq[1:])), default=0),
    }


def nearest_pts(timestamp: float, pts_values: list[float]) -> tuple[int, float]:
    """Return nearest source-frame index and signed capture-minus-PTS delta."""
    if not pts_values:
        raise ValueError("No source PTS values supplied")
    index = bisect.bisect_left(pts_values, timestamp)
    candidates = [i for i in (index - 1, index) if 0 <= i < len(pts_values)]
    best = min(candidates, key=lambda i: abs(pts_values[i] - timestamp))
    return best, timestamp - pts_values[best]


def run_ffprobe(ffprobe: Path, source: Path) -> tuple[list[float], dict[str, Any]]:
    command = [
        str(ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time",
        "-of", "json", str(source),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {source.name}: {result.stderr[-1200:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {source.name}") from exc
    frames = payload.get("frames", [])
    pts = []
    for frame in frames:
        value = frame.get("best_effort_timestamp_time")
        if _finite(value):
            pts.append(float(value))
    if len(pts) < 2:
        raise RuntimeError(f"ffprobe returned fewer than two valid best_effort_timestamp_time values for {source.name}")
    if any(b < a for a, b in zip(pts, pts[1:])):
        raise RuntimeError(f"Source best_effort_timestamp_time is not monotonic for {source.name}")
    steps = [b - a for a, b in zip(pts, pts[1:]) if b > a]
    metadata = {
        "ffprobe_frame_records": len(frames),
        "ffprobe_valid_pts_count": len(pts),
        "source_pts_step_median_seconds": statistics.median(steps) if steps else None,
        "source_pts_step_min_seconds": min(steps) if steps else None,
        "source_pts_step_max_seconds": max(steps) if steps else None,
    }
    return pts, metadata


def locate_source(data_root: Path, video_path: str) -> Path:
    normalized = normalize_video_path(video_path)
    expected = data_root.joinpath(*normalized.split("/"))
    if expected.is_file():
        return expected
    expected_stem = Path(normalized).stem.casefold()
    matches = [p for p in data_root.rglob("*") if p.is_file() and p.stem.casefold() == expected_stem]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        suffix = Path(*normalized.split("/")).parent.parts
        suffix_norm = tuple(part.casefold() for part in suffix)
        narrowed = [p for p in matches if tuple(part.casefold() for part in p.relative_to(data_root).parts[-(len(suffix_norm)+1):-1]) == suffix_norm]
        if len(narrowed) == 1:
            return narrowed[0]
    raise FileNotFoundError(f"Could not uniquely resolve source video for {video_path}; matches={len(matches)}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_capture(mode_dir: Path, expected_mode: str, row: dict[str, str], index_rows: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], tuple[bytes, ...], str]:
    key = normalize_video_path(row["video_path"]).casefold()
    record = index_rows.get(key)
    if not record:
        raise FileNotFoundError(f"Capture index has no row for {row['video_path']} in {mode_dir}")
    rel = Path(record.get("container", ""))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe capture path in index: {rel}")
    path = (mode_dir / rel).resolve()
    path.relative_to(mode_dir.resolve())
    payload = path.read_bytes()
    parsed = parse_capture_payload(payload, row)
    if parsed.sha256 != record.get("sha256"):
        raise ValueError(f"Capture SHA-256 mismatch for {row['video_path']} / {mode_dir.name}")
    header_mode = parsed.header.get("capture_config", {}).get("phase7_mode")
    case_mode = parsed.header.get("case", {}).get("phase7_mode")
    if header_mode != expected_mode or case_mode != expected_mode:
        raise ValueError(f"Capture mode mismatch: expected={expected_mode}, header={header_mode}, case={case_mode}")
    return parsed.header["case"], parsed.frames, parsed.sha256


def _values(frames: list[dict[str, Any]], field: str) -> list[float]:
    return [float(f[field]) for f in frames if _finite(f.get(field))]


def summarize_clip(mode: str, case: dict[str, Any], frame_rows: list[dict[str, Any]], pts: list[float], pts_meta: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frame_pts_indices: list[int] = []
    frame_deltas: list[float] = []
    per_frame: list[dict[str, Any]] = []
    prev_pf = None
    prev_callback = None
    for index, frame in enumerate(frame_rows):
        media_time = float(frame["video_time_seconds"])
        pts_index, signed_delta = nearest_pts(media_time, pts)
        frame_pts_indices.append(pts_index)
        frame_deltas.append(signed_delta)
        presented = int(frame.get("presented_frames", 0))
        presented_gap = max(0, presented - prev_pf - 1) if prev_pf is not None else max(0, presented - 1)
        callback_time = float(frame.get("callback_time_ms", 0.0))
        callback_gap = callback_time - prev_callback if prev_callback is not None else None
        per_frame.append({
            "capture_mode": mode,
            "video_path": case["video_path"],
            "label": case["label"],
            "capture_frame_index": index,
            "media_time_seconds": media_time,
            "capture_current_time_seconds": frame.get("capture_current_time_seconds"),
            "presented_frames": presented,
            "presented_frame_gap_from_previous": presented_gap,
            "callback_time_ms": callback_time,
            "callback_gap_ms": callback_gap,
            "callback_work_ms": frame.get("callback_work_ms"),
            "draw_duration_ms": frame.get("draw_duration_ms"),
            "encode_latency_ms": frame.get("encode_latency_ms"),
            "pending_encodes_at_enqueue": frame.get("pending_encodes_at_enqueue"),
            "jpeg_bytes": frame.get("jpeg_bytes"),
            "source_pts_frame_index": pts_index,
            "source_best_effort_pts_seconds": pts[pts_index],
            "capture_minus_source_pts_ms": signed_delta * 1000.0,
        })
        prev_pf = presented
        prev_callback = callback_time

    presented_seq = [int(f.get("presented_frames", 0)) for f in frame_rows]
    total_presented = int(case.get("browser_total_video_frames", 0))
    gap_stats = sequence_gap_metrics(presented_seq, total_presented if total_presented > 0 else None)
    callback_gaps = [v for v in _values(frame_rows, "callback_time_ms")]
    callback_gaps = [b - a for a, b in zip(callback_gaps, callback_gaps[1:])]
    unique_pts = len(set(frame_pts_indices))
    pts_delta_abs = [abs(x) * 1000.0 for x in frame_deltas]
    pts_step = pts_meta.get("source_pts_step_median_seconds")
    half_frame_tolerance_ms = (float(pts_step) * 500.0) if _finite(pts_step) else None
    in_tolerance = sum(1 for d in pts_delta_abs if half_frame_tolerance_ms is not None and d <= half_frame_tolerance_ms + 1e-6)
    total_frames = int(case.get("browser_total_video_frames", 0))
    captured = len(frame_rows)
    summary = {
        "capture_mode": mode,
        "video_path": case["video_path"],
        "label": case["label"],
        "capture_frame_count": captured,
        "rVFC_callback_count": int(case.get("timer_ticks", 0)),
        "callbacks_not_captured": max(0, int(case.get("timer_ticks", 0)) - captured),
        "browser_total_video_frames": total_frames,
        "browser_dropped_video_frames": int(case.get("browser_dropped_video_frames", 0)),
        "capture_coverage_pct_of_browser_total": (100.0 * captured / total_frames) if total_frames else None,
        "unseen_presented_frames": gap_stats["unseen_presented_frames"] if total_frames else None,
        "interior_presented_frame_gaps": gap_stats["interior_presented_frame_gaps"],
        "max_interior_presented_frame_gap": gap_stats["max_interior_gap"],
        "duplicate_frame_skips": int(case.get("duplicate_frame_skips", 0)),
        "rate_limited_callback_skips": int(case.get("rate_limited_callback_skips", 0)),
        "duplicate_media_callback_skips": int(case.get("duplicate_media_callback_skips", 0)),
        "capture_duration_ms": float(case.get("capture_duration_ms", 0.0)),
        "callback_gap_median_ms": percentile(callback_gaps, 0.5),
        "callback_gap_p95_ms": percentile(callback_gaps, 0.95),
        "callback_gap_max_ms": max(callback_gaps) if callback_gaps else None,
        "callback_work_mean_ms": case.get("callback_work_mean_ms"),
        "callback_work_p95_ms": percentile(_values(frame_rows, "callback_work_ms"), 0.95),
        "draw_duration_mean_ms": case.get("draw_duration_mean_ms"),
        "draw_duration_p95_ms": percentile(_values(frame_rows, "draw_duration_ms"), 0.95),
        "encode_latency_mean_ms": case.get("encode_latency_mean_ms"),
        "encode_latency_p95_ms": percentile(_values(frame_rows, "encode_latency_ms"), 0.95),
        "max_pending_encodes": int(case.get("max_pending_encodes", 0)),
        "mean_pending_encodes_at_enqueue": float(case.get("mean_pending_encodes_at_enqueue", 0.0)),
        "source_pts_frame_count": len(pts),
        "unique_source_pts_frames_matched": unique_pts,
        "source_pts_unique_coverage_pct": (100.0 * unique_pts / len(pts)) if pts else None,
        "pts_matches_within_half_frame": in_tolerance,
        "pts_match_within_half_frame_pct": (100.0 * in_tolerance / captured) if captured else None,
        "pts_abs_delta_median_ms": percentile(pts_delta_abs, 0.5),
        "pts_abs_delta_p95_ms": percentile(pts_delta_abs, 0.95),
        "pts_abs_delta_max_ms": max(pts_delta_abs) if pts_delta_abs else None,
        "source_pts_step_median_ms": (float(pts_step) * 1000.0) if _finite(pts_step) else None,
        "source_video_sha256": case.get("source_video_sha256"),
        "capture_sha256": case.get("capture_sha256"),
    }
    return summary, per_frame


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_report(mode_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]], source_notes: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    def total(mode: str, key: str) -> float:
        return sum(float(r.get(key) or 0) for r in mode_rows if r["capture_mode"] == mode)
    lines = [
        "# Phase 7 — Capture completeness and encoding back-pressure report", "",
        f"Generated: {metadata['generated_at_local']}", "",
        "## Scope and guardrails", "",
        "This is a diagnostic-only comparison across the same six INCLUDE-50 clips. It does not load the classifier/checkpoint, call the production recognition path, change the feature contract, or connect to IoT/MQTT/ESP32. The two capture modes are stored separately.", "",
        "- Mode A (`current_40ms`): requestVideoFrameCallback, 39 ms minimum interval, synchronous `toDataURL` JPEG encoding.",
        "- Mode B (`every_presented_frame`): no minimum interval; synchronous `drawImage` inside the callback; asynchronous `toBlob` JPEG encoding. Callback processing does not wait for the encode completion.",
        "- Both modes use a 640 px Canvas width and JPEG quality 0.92. Source alignment uses FFprobe `best_effort_timestamp_time`.", "",
        "## Aggregate results", "",
        "| Metric | Mode A — current 40 ms | Mode B — every callback |",
        "|---|---:|---:|",
        f"| Captured frames | {int(total('current_40ms','capture_frame_count'))} | {int(total('every_presented_frame','capture_frame_count'))} |",
        f"| Browser total presented frames | {int(total('current_40ms','browser_total_video_frames'))} | {int(total('every_presented_frame','browser_total_video_frames'))} |",
        f"| Unseen presented frames (summed) | {int(total('current_40ms','unseen_presented_frames'))} | {int(total('every_presented_frame','unseen_presented_frames'))} |",
        f"| Browser-reported dropped frames | {int(total('current_40ms','browser_dropped_video_frames'))} | {int(total('every_presented_frame','browser_dropped_video_frames'))} |",
        f"| FFprobe PTS matched frames | {int(total('current_40ms','pts_matches_within_half_frame'))} | {int(total('every_presented_frame','pts_matches_within_half_frame'))} |",
        f"| Max asynchronous encode queue (largest per clip) | 0 | {max([int(r['max_pending_encodes']) for r in mode_rows if r['capture_mode']=='every_presented_frame'] or [0])} |",
        "",
        "These are summed counts over six clips. Per-clip capture duration and browser total-frame counts are retained in the CSVs. Capture coverage is not recognition accuracy.", "",
        "## Per-clip comparison", "",
        "| Clip | A frames / total | B frames / total | A unseen | B unseen | A callback p95 ms | B callback p95 ms | B encode p95 ms | B max queue |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    by_key = {(r['video_path'], r['capture_mode']): r for r in mode_rows}
    for path in TARGET_PATHS:
        a = by_key.get((path, 'current_40ms'), {})
        b = by_key.get((path, 'every_presented_frame'), {})
        label = Path(path).stem
        lines.append(
            f"| {label} | {a.get('capture_frame_count','—')} / {a.get('browser_total_video_frames','—')} | "
            f"{b.get('capture_frame_count','—')} / {b.get('browser_total_video_frames','—')} | "
            f"{a.get('unseen_presented_frames','—')} | {b.get('unseen_presented_frames','—')} | "
            f"{_fmt(a.get('callback_gap_p95_ms'))} | {_fmt(b.get('callback_gap_p95_ms'))} | "
            f"{_fmt(b.get('encode_latency_p95_ms'))} | {b.get('max_pending_encodes','—')} |"
        )
    lines += ["", "## Interpretation guide", "",
              "- Lower unseen-presented-frame counts in Mode B would support the hypothesis that the 39 ms gate and synchronous `toDataURL` path limit capture completeness.",
              "- If Mode B still has gaps while browser-reported dropped frames remain zero, inspect callback work duration, callback gaps, queue depth, and presented-frame sequence gaps. That indicates capture callback/processing constraints rather than a proven video-decoder drop.",
              "- If Mode B increases captured frames but its asynchronous encode queue grows continuously or p95 encode latency approaches clip duration, the encoder becomes a back-pressure/memory risk. Do not promote it to production without a bounded-queue design and another test.",
              "- `presentedFrames` is a browser compositor counter, while FFprobe PTS indexes source decoded frames. They are complementary measurements and should not be conflated.",
              "- `best_effort_timestamp_time` alignment within half a source-frame interval verifies timestamp association; it does not prove every source frame was presented or captured.", "",
              "## Source video verification", "",
              "| Clip | Source frames reported by FFprobe | Median PTS step ms | SHA-256 |",
              "|---|---:|---:|---|"]
    for item in source_notes:
        lines.append(f"| {Path(item['video_path']).stem} | {item['ffprobe_valid_pts_count']} | {_fmt(float(item['source_pts_step_median_seconds'])*1000 if item.get('source_pts_step_median_seconds') else None)} | `{item['source_video_sha256']}` |")
    lines += ["", "## Engineering decision", "",
              "This report is diagnostic evidence only. It does not authorize changing the production capture loop. Any proposed production change must preserve capture integrity, bound memory/encoding backlog, retain the established 640 px / JPEG q92 / 134-D / 169-frame model contract unless independently revalidated, and remain IoT-disabled during verification.", ""]
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "—" if value is None or not _finite(value) else f"{float(value):.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode-a-dir", type=Path, required=True)
    parser.add_argument("--mode-b-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    args = parser.parse_args()

    for path, name in ((args.repo, "AI4Bharat repo"), (args.data_root, "INCLUDE data root"), (args.manifest, "official manifest"), (args.mode_a_dir, "Mode A capture directory"), (args.mode_b_dir, "Mode B capture directory"), (args.ffprobe, "ffprobe executable")):
        if not path.exists():
            raise FileNotFoundError(f"{name} missing: {path}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    label_map = ROOT / "config" / "label_map_include50.json"
    rows = read_and_verify_manifest(args.manifest.resolve(), args.repo.resolve(), label_map.resolve())
    target_map = {normalize_video_path(r["video_path"]).casefold(): r for r in rows}
    selected = [target_map[p.casefold()] for p in TARGET_PATHS]
    mode_dirs = {"current_40ms": args.mode_a_dir.resolve(), "every_presented_frame": args.mode_b_dir.resolve()}

    index_maps: dict[str, dict[str, dict[str, Any]]] = {}
    for mode, directory in mode_dirs.items():
        index_path = directory / "capture_index.json"
        if not index_path.is_file():
            raise FileNotFoundError(f"Capture index missing: {index_path}")
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        if index_data.get("capture_config", {}).get("phase7_mode") != mode:
            raise ValueError(f"Capture index mode mismatch in {index_path}")
        if index_data.get("received_count") != 6:
            raise ValueError(f"Expected six captures in {directory}; found {index_data.get('received_count')}")
        index_maps[mode] = {normalize_video_path(r["video_path"]).casefold(): r for r in index_data.get("received", [])}

    ffprobe = args.ffprobe.resolve()
    source_cache: dict[str, tuple[Path, list[float], dict[str, Any], str]] = {}
    for row in selected:
        key = normalize_video_path(row["video_path"]).casefold()
        source = locate_source(args.data_root.resolve(), row["video_path"])
        pts, pts_meta = run_ffprobe(ffprobe, source)
        source_hash = sha256_file(source)
        source_cache[key] = (source, pts, pts_meta, source_hash)

    clip_rows: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    source_notes: list[dict[str, Any]] = []
    for row in selected:
        key = normalize_video_path(row["video_path"]).casefold()
        source, pts, pts_meta, source_hash = source_cache[key]
        source_notes.append({"video_path": row["video_path"], **pts_meta, "source_video_sha256": source_hash})
        for mode, directory in mode_dirs.items():
            case, _jpeg_frames, capture_hash = load_capture(directory, mode, row, index_maps[mode])
            case["capture_sha256"] = capture_hash
            case["source_video_sha256"] = source_hash
            if case.get("source_name", "").casefold() != source.name.casefold():
                # The official manifest may use .MOV while local data resolution
                # can map a uniquely resolved alternate extension. Keep both explicit.
                case["resolved_source_name"] = source.name
            summary, per_frame = summarize_clip(mode, case, case["frames"], pts, pts_meta)
            summary["resolved_source_path"] = str(source)
            summary["source_video_sha256"] = source_hash
            clip_rows.append(summary)
            frame_rows.extend(per_frame)

    pair_rows: list[dict[str, Any]] = []
    by_pair = {(r["video_path"], r["capture_mode"]): r for r in clip_rows}
    for path in TARGET_PATHS:
        a = by_pair[(path, "current_40ms")]
        b = by_pair[(path, "every_presented_frame")]
        pair_rows.append({
            "video_path": path,
            "current_40ms_frames": a["capture_frame_count"],
            "every_presented_frame_frames": b["capture_frame_count"],
            "captured_frame_gain": b["capture_frame_count"] - a["capture_frame_count"],
            "current_40ms_unseen_presented": a["unseen_presented_frames"],
            "every_presented_frame_unseen_presented": b["unseen_presented_frames"],
            "unseen_presented_reduction": (a["unseen_presented_frames"] or 0) - (b["unseen_presented_frames"] or 0),
            "current_40ms_browser_dropped": a["browser_dropped_video_frames"],
            "every_presented_frame_browser_dropped": b["browser_dropped_video_frames"],
            "current_40ms_callback_gap_p95_ms": a["callback_gap_p95_ms"],
            "every_presented_frame_callback_gap_p95_ms": b["callback_gap_p95_ms"],
            "every_presented_frame_encode_latency_p95_ms": b["encode_latency_p95_ms"],
            "every_presented_frame_max_pending_encodes": b["max_pending_encodes"],
            "current_40ms_pts_half_frame_match_pct": a["pts_match_within_half_frame_pct"],
            "every_presented_frame_pts_half_frame_match_pct": b["pts_match_within_half_frame_pct"],
        })

    totals = {}
    for mode in MODES:
        subset = [r for r in clip_rows if r["capture_mode"] == mode]
        totals[mode] = {
            "clip_count": len(subset),
            "captured_frames": sum(r["capture_frame_count"] for r in subset),
            "rVFC_callbacks": sum(r["rVFC_callback_count"] for r in subset),
            "browser_total_video_frames": sum(r["browser_total_video_frames"] for r in subset),
            "unseen_presented_frames": sum(r["unseen_presented_frames"] or 0 for r in subset),
            "browser_dropped_video_frames": sum(r["browser_dropped_video_frames"] for r in subset),
            "pts_matches_within_half_frame": sum(r["pts_matches_within_half_frame"] for r in subset),
            "max_pending_encodes_any_clip": max((r["max_pending_encodes"] for r in subset), default=0),
        }

    summary = {
        "status": "completed",
        "phase": 7,
        "generated_at_local": __import__("datetime").datetime.now().astimezone().isoformat(),
        "official_validation_manifest_count": len(rows),
        "official_validation_targets": 6,
        "ai4bharat_commit": "df348f4a640060d4d39f02556d9f96851e8c46c7",
        "ffprobe_executable": str(ffprobe),
        "ffprobe_version": subprocess.run([str(ffprobe), "-version"], capture_output=True, text=True, check=False).stdout.splitlines()[0],
        "classifier_checkpoint_loaded": False,
        "iot_enabled": False,
        "production_code_modified": False,
        "mode_totals": totals,
        "clips": clip_rows,
        "pairwise_comparison": pair_rows,
        "source_videos": source_notes,
    }
    summary_path = args.output_dir / "phase7_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    write_csv(args.output_dir / "phase7_clip_comparison.csv", clip_rows)
    write_csv(args.output_dir / "phase7_frame_metrics.csv", frame_rows)
    write_csv(args.output_dir / "phase7_mode_pairwise_comparison.csv", pair_rows)
    write_csv(args.output_dir / "phase7_source_pts_metadata.csv", source_notes)
    report = build_report(clip_rows, pair_rows, source_notes, summary)
    (args.output_dir / "PHASE7_CAPTURE_COMPLETENESS_REPORT.md").write_text(report, encoding="utf-8")

    print("PHASE7_STATUS=completed")
    print("CLIPS=6/6 per mode; variants=2")
    for mode in MODES:
        values = totals[mode]
        print(f"MODE={mode} | captured={values['captured_frames']} | browser_total={values['browser_total_video_frames']} | unseen={values['unseen_presented_frames']} | dropped={values['browser_dropped_video_frames']} | PTS_MATCHED={values['pts_matches_within_half_frame']}")
    print(f"OUTPUT={args.output_dir.resolve()}")
    print("IoT stayed disabled; classifier/checkpoint was not loaded; production code unchanged.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PHASE7_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
