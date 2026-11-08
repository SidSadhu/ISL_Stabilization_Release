#!/usr/bin/env python3
"""Analyze Phase 8 2x2 capture experiment using FFprobe source-frame coverage.

This deliberately does not compare requestVideoFrameCallback.presentedFrames to
getVideoPlaybackQuality.totalVideoFrames. Those are distinct browser counters.
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
MODES = ("gate_sync", "ungate_sync", "gate_async", "ungate_async")
MODE_FACTORS = {
    "gate_sync": ("gated", "sync"),
    "ungate_sync": ("ungated", "sync"),
    "gate_async": ("gated", "async"),
    "ungate_async": ("ungated", "async"),
}


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def pct(values: list[float], quantile: float) -> float | None:
    values = sorted(float(x) for x in values if finite(x))
    if not values:
        return None
    return values[min(len(values) - 1, max(0, math.ceil(quantile * len(values)) - 1))]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


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
        parent_parts = tuple(x.casefold() for x in Path(*normalized.split("/")).parent.parts)
        narrowed = [p for p in matches if tuple(x.casefold() for x in p.relative_to(data_root).parts[-(len(parent_parts)+1):-1]) == parent_parts]
        if len(narrowed) == 1:
            return narrowed[0]
    raise FileNotFoundError(f"Could not uniquely resolve source video for {video_path}; matches={len(matches)}")


def ffprobe_pts(ffprobe: Path, source: Path) -> tuple[list[float], dict[str, Any]]:
    command = [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_frames",
               "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise RuntimeError(f"FFprobe failed for {source.name}: {result.stderr[-1000:]}")
    payload = json.loads(result.stdout)
    all_frames = payload.get("frames", [])
    pts = [float(f["best_effort_timestamp_time"]) for f in all_frames if finite(f.get("best_effort_timestamp_time"))]
    if len(pts) < 2 or any(b <= a for a, b in zip(pts, pts[1:])):
        raise RuntimeError(f"Invalid/non-monotonic FFprobe PTS for {source}")
    steps = [b-a for a,b in zip(pts,pts[1:])]
    return pts, {
        "ffprobe_frame_records": len(all_frames), "source_frame_count": len(pts),
        "pts_step_median_ms": statistics.median(steps)*1000,
        "source_video_sha256": sha256_file(source),
    }


def nearest_index(ts: float, pts: list[float]) -> tuple[int, float]:
    pos = bisect.bisect_left(pts, ts)
    candidates = [i for i in (pos-1, pos) if 0 <= i < len(pts)]
    idx = min(candidates, key=lambda i: abs(pts[i]-ts))
    return idx, ts - pts[idx]


def missing_ranges(indexes: list[int], count: int) -> list[list[int]]:
    missing = sorted(set(range(count)) - set(indexes))
    spans: list[list[int]] = []
    for value in missing:
        if not spans or value > spans[-1][-1] + 1:
            spans.append([value])
        else:
            spans[-1].append(value)
    return [[span[0], span[-1]] for span in spans]


def load_capture(mode_dir: Path, expected_mode: str, row: dict[str, str], record: dict[str, Any]) -> dict[str, Any]:
    rel = Path(record.get("container", ""))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe capture container path: {rel}")
    container = (mode_dir / rel).resolve()
    container.relative_to(mode_dir.resolve())
    payload = container.read_bytes()
    parsed = parse_capture_payload(payload, row)
    if parsed.sha256 != record.get("sha256"):
        raise ValueError(f"Capture SHA-256 mismatch for {row['video_path']} / {expected_mode}")
    case = parsed.header["case"]
    config = parsed.header["capture_config"]
    if case.get("phase8_mode") != expected_mode or config.get("phase8_mode") != expected_mode:
        raise ValueError(f"Phase 8 mode identity mismatch for {row['video_path']} / {expected_mode}")
    return {"case": case, "frames": parsed.frames, "sha256": parsed.sha256, "container": str(container)}


def summarize_capture(mode: str, case: dict[str, Any], pts: list[float], pts_meta: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    factors = MODE_FACTORS[mode]
    frame_rows = case["frames"]
    mapped: list[tuple[int, float, dict[str, Any]]] = []
    out_frames: list[dict[str, Any]] = []
    for i, frame in enumerate(frame_rows):
        idx, delta = nearest_index(float(frame["video_time_seconds"]), pts)
        mapped.append((idx, delta, frame))
        out_frames.append({
            "mode": mode, "gate_factor": factors[0], "encoder_factor": factors[1],
            "video_path": case["video_path"], "label": case["label"], "capture_frame_index": i,
            "media_time_seconds": frame["video_time_seconds"], "source_pts_frame_index": idx,
            "source_best_effort_pts_seconds": pts[idx], "capture_minus_pts_ms": delta*1000,
            "presented_frames_counter": frame.get("presented_frames"),
            "callback_time_ms": frame.get("callback_time_ms"), "callback_lateness_ms": frame.get("callback_lateness_ms"),
            "elapsed_ms": frame.get("elapsed_ms"), "callback_work_ms": frame.get("callback_work_ms"),
            "draw_duration_ms": frame.get("draw_duration_ms"), "encode_latency_ms": frame.get("encode_latency_ms"),
            "pending_encodes_at_enqueue": frame.get("pending_encodes_at_enqueue"),
            "visibility_state": frame.get("visibility_state"), "has_focus": frame.get("has_focus"),
            "video_ready_state": frame.get("video_ready_state"), "video_paused": frame.get("video_paused"),
            "playback_rate": frame.get("playback_rate"), "jpeg_bytes": frame.get("jpeg_bytes"),
        })
    indexes = [x[0] for x in mapped]
    unique = sorted(set(indexes))
    ranges = missing_ranges(unique, len(pts))
    media_times = [float(x["video_time_seconds"]) for x in frame_rows]
    frame_elapsed = [float(x.get("elapsed_ms", 0)) for x in frame_rows]
    callback_times = [float(x.get("callback_time_ms", 0)) for x in frame_rows]
    callback_gaps = [b-a for a,b in zip(callback_times,callback_times[1:])]
    callback_work = [float(x["callback_work_ms"]) for x in frame_rows if finite(x.get("callback_work_ms"))]
    draws = [float(x["draw_duration_ms"]) for x in frame_rows if finite(x.get("draw_duration_ms"))]
    encodes = [float(x["encode_latency_ms"]) for x in frame_rows if finite(x.get("encode_latency_ms"))]
    presented = [int(x.get("presented_frames",0)) for x in frame_rows]
    presented_gaps = [max(0,b-a-1) for a,b in zip(presented,presented[1:])]
    heartbeats = case.get("heartbeat_samples", [])
    hb_times = [float(x.get("elapsed_ms",0)) for x in heartbeats]
    hb_gaps = [b-a for a,b in zip(hb_times,hb_times[1:])]
    visible_frames = sum(1 for x in frame_rows if x.get("visibility_state") == "visible")
    focused_frames = sum(1 for x in frame_rows if x.get("has_focus") is True)
    events = case.get("timeline_events", [])
    event_counts: dict[str,int] = {}
    for e in events:
        k = str(e.get("event_type","unknown")); event_counts[k]=event_counts.get(k,0)+1
    hidden_events = sum(1 for e in events if e.get("event_type") == "document_visibilitychange" and e.get("visibility_state") != "visible")
    blur_events = event_counts.get("window_blur",0)
    q0 = case.get("quality_before") or {}
    q1 = case.get("quality_at_end") or {}
    qtotal = max(0,int(q1.get("total_video_frames",0))-int(q0.get("total_video_frames",0)))
    qdrop = max(0,int(q1.get("dropped_video_frames",0))-int(q0.get("dropped_video_frames",0)))
    last_elapsed = max(frame_elapsed) if frame_elapsed else 0.0
    capture_duration = float(case.get("capture_duration_ms",0))
    tail_silence = max(0.0,capture_duration-last_elapsed)
    source_count = len(pts)
    abs_deltas = [abs(x[1])*1000 for x in mapped]
    half = float(pts_meta["pts_step_median_ms"])/2
    in_tolerance = sum(1 for d in abs_deltas if d <= half + 1e-6)
    summary = {
        "mode": mode, "gate_factor": factors[0], "encoder_factor": factors[1],
        "video_path": case["video_path"], "label": case["label"],
        "source_frame_count": source_count, "captured_frame_count": len(frame_rows),
        "unique_source_frames_captured": len(unique), "source_frame_coverage_pct": 100*len(unique)/source_count,
        "missing_source_frame_count": source_count-len(unique), "missing_source_frame_ranges": ranges,
        "first_source_frame_index": min(unique) if unique else None, "last_source_frame_index": max(unique) if unique else None,
        "leading_source_frames_missing": min(unique) if unique else source_count,
        "trailing_source_frames_missing": (source_count-1-max(unique)) if unique else source_count,
        "pts_match_within_half_frame_pct": 100*in_tolerance/max(1,len(frame_rows)),
        "pts_abs_delta_median_ms": statistics.median(abs_deltas) if abs_deltas else None,
        "rVFC_requested_count": int(case.get("callback_requested_count",0)),
        "rVFC_delivered_count": int(case.get("callback_delivered_count",case.get("timer_ticks",0))),
        "callbacks_scheduled_not_delivered": int(case.get("callbacks_scheduled_not_delivered",0)),
        "callbacks_rejected_by_gate": int(case.get("rate_limited_callback_skips",0)),
        "duplicate_media_callbacks": int(case.get("duplicate_media_callback_skips",0)),
        "presentedFrames_interior_gap_total": sum(presented_gaps),
        "presentedFrames_max_interior_gap": max(presented_gaps,default=0),
        "capture_duration_ms": capture_duration, "first_frame_elapsed_ms": min(frame_elapsed) if frame_elapsed else None,
        "last_frame_elapsed_ms": last_elapsed, "callback_tail_silence_ms": tail_silence,
        "callback_gap_median_ms": pct(callback_gaps,0.5), "callback_gap_p95_ms": pct(callback_gaps,0.95),
        "callback_gap_max_ms": max(callback_gaps,default=None), "callback_lateness_p95_ms": pct([float(x.get("callback_lateness_ms",0)) for x in frame_rows],0.95),
        "callback_work_p95_ms": pct(callback_work,0.95), "draw_p95_ms": pct(draws,0.95),
        "encode_latency_median_ms": statistics.median(encodes) if encodes else None,
        "encode_latency_p95_ms": pct(encodes,0.95), "encode_latency_max_ms": max(encodes,default=None),
        "max_pending_encodes": int(case.get("max_pending_encodes",0)),
        "visible_frame_pct": 100*visible_frames/max(1,len(frame_rows)),
        "focused_frame_pct": 100*focused_frames/max(1,len(frame_rows)),
        "hidden_transition_events": hidden_events, "window_blur_events": blur_events,
        "heartbeat_count": len(heartbeats), "heartbeat_gap_p95_ms": pct(hb_gaps,0.95),
        "heartbeat_gap_max_ms": max(hb_gaps,default=None), "video_duration_seconds": float(case.get("source_duration_seconds",0)),
        "video_current_time_at_end": case.get("video_current_time_at_end"),
        "video_ended_at_finish": bool(case.get("video_ended_at_finish")),
        "page_visibility_at_finish": case.get("page_visibility_at_finish"), "page_has_focus_at_finish": case.get("page_has_focus_at_finish"),
        "play_promise_resolve_ms": case.get("play_promise_resolve_ms"),
        "quality_total_video_frames_delta": qtotal, "quality_dropped_video_frames_delta": qdrop,
        "rvfc_silence_event_count": len(case.get("rvfc_silence_events",[])),
        "video_waiting_events": event_counts.get("video_waiting",0), "video_stalled_events": event_counts.get("video_stalled",0),
        "video_timeupdate_events": event_counts.get("video_timeupdate",0),
        "source_video_sha256": pts_meta.get("source_video_sha256"), "capture_sha256": case.get("capture_sha256"),
    }
    event_rows = []
    for event in events:
        event_rows.append({"mode":mode,"video_path":case["video_path"],**event})
    heartbeat_rows = []
    for hb in heartbeats:
        heartbeat_rows.append({"mode":mode,"video_path":case["video_path"],**hb})
    for row in ranges:
        pass
    return summary, out_frames, event_rows, heartbeat_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("",encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        writer.writeheader();writer.writerows(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo=args.repo.resolve(); manifest=args.manifest.resolve(); data_root=args.data_root.resolve(); output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    official=read_and_verify_manifest(manifest,repo,(ROOT/"config"/"label_map_include50.json").resolve())
    row_map={r["video_path"].casefold():r for r in official}
    target_rows=[row_map[p.casefold()] for p in TARGET_PATHS]
    mode_dirs={mode:getattr(args,mode) for mode in MODES}
    for mode,d in mode_dirs.items():
        if not d.is_dir(): raise FileNotFoundError(f"Missing capture directory for {mode}: {d}")
    pts_by_path={}; pts_meta_by_path={}; source_by_path={}
    for row in target_rows:
        source=locate_source(data_root,row["video_path"]); pts,meta=ffprobe_pts(args.ffprobe,source)
        pts_by_path[row["video_path"]]=pts; pts_meta_by_path[row["video_path"]]=meta; source_by_path[row["video_path"]]=source
    clip_summaries=[]; frame_rows=[]; event_rows=[]; heartbeat_rows=[]; capture_hashes={}
    for mode,directory in mode_dirs.items():
        index=json.loads((directory/"capture_index.json").read_text(encoding="utf-8-sig"))
        if index.get("official_split_count")!=77 or index.get("received_count")!=6: raise ValueError(f"{mode} capture index must verify six of 77 pinned rows")
        records={normalize_video_path(r["video_path"]).casefold():r for r in index.get("received",[])}
        for row in target_rows:
            key=normalize_video_path(row["video_path"]).casefold()
            if key not in records: raise FileNotFoundError(f"{mode}: missing capture for {row['video_path']}")
            loaded=load_capture(directory,mode,row,records[key]); case=loaded["case"]
            pts=pts_by_path[row["video_path"]]; ptsmeta=pts_meta_by_path[row["video_path"]]
            summary,frames,events,heartbeats=summarize_capture(mode,case,pts,ptsmeta)
            summary["capture_sha256"]=loaded["sha256"]
            summary["source_video_sha256"]=ptsmeta["source_video_sha256"]
            clip_summaries.append(summary);frame_rows.extend(frames);event_rows.extend(events);heartbeat_rows.extend(heartbeats)
            capture_hashes[f"{mode}/{Path(records[key]['container']).name}"]=loaded["sha256"]
    # Pairwise 2x2 factor effects per clip.
    by={(r["video_path"],r["mode"]):r for r in clip_summaries}
    effects=[]
    for path in TARGET_PATHS:
        a=by[(path,"gate_sync")]; b=by[(path,"ungate_sync")]; c=by[(path,"gate_async")]; d=by[(path,"ungate_async")]
        effects.append({
            "video_path":path,
            "gate_effect_sync_encoder_pp":b["source_frame_coverage_pct"]-a["source_frame_coverage_pct"],
            "gate_effect_async_encoder_pp":d["source_frame_coverage_pct"]-c["source_frame_coverage_pct"],
            "async_encoder_effect_when_gated_pp":c["source_frame_coverage_pct"]-a["source_frame_coverage_pct"],
            "async_encoder_effect_when_ungated_pp":d["source_frame_coverage_pct"]-b["source_frame_coverage_pct"],
            "callback_tail_silence_ms_by_mode":{m:by[(path,m)]["callback_tail_silence_ms"] for m in MODES},
        })
    factorial=[]
    for factor, pairs in {
        "gate_effect_with_sync_encoder": [("ungate_sync","gate_sync")],
        "gate_effect_with_async_encoder": [("ungate_async","gate_async")],
        "async_vs_sync_when_gated": [("gate_async","gate_sync")],
        "async_vs_sync_when_ungated": [("ungate_async","ungate_sync")],
    }.items():
        diffs=[]
        for plus,minus in pairs:
            diffs.extend(by[(p,plus)]["source_frame_coverage_pct"]-by[(p,minus)]["source_frame_coverage_pct"] for p in TARGET_PATHS)
        factorial.append({"contrast":factor,"mean_clip_coverage_difference_pp":statistics.mean(diffs),"median_clip_difference_pp":statistics.median(diffs),"clips_positive":sum(x>0 for x in diffs),"clips_negative":sum(x<0 for x in diffs),"clip_count":len(diffs)})
    mode_totals={}
    for mode in MODES:
        subset=[r for r in clip_summaries if r["mode"]==mode]
        captured=sum(r["unique_source_frames_captured"] for r in subset); source_count=sum(r["source_frame_count"] for r in subset)
        mode_totals[mode]={"clip_count":len(subset),"captured_unique_source_frames":captured,"source_frames":source_count,"weighted_source_frame_coverage_pct":100*captured/source_count,
                           "mean_clip_coverage_pct":statistics.mean(r["source_frame_coverage_pct"] for r in subset),
                           "rVFC_delivered":sum(r["rVFC_delivered_count"] for r in subset),
                           "callback_gate_skips":sum(r["callbacks_rejected_by_gate"] for r in subset),
                           "mean_callback_tail_silence_ms":statistics.mean(r["callback_tail_silence_ms"] for r in subset),
                           "browser_quality_dropped_delta_sum":sum(r["quality_dropped_video_frames_delta"] for r in subset),
                           "mean_encode_p95_ms":statistics.mean([r["encode_latency_p95_ms"] for r in subset if r["encode_latency_p95_ms"] is not None]) if any(r["encode_latency_p95_ms"] is not None for r in subset) else None}
    source_rows=[]
    for p,meta in pts_meta_by_path.items(): source_rows.append({"video_path":p,**meta})
    summary={"status":"completed","phase":8,"generated_at_local":None,"official_validation_manifest_count":len(official),"official_validation_targets":6,
             "classifier_checkpoint_loaded":False,"iot_enabled":False,"production_code_modified":False,
             "measurement_note":"Capture coverage uses unique FFprobe best_effort_timestamp_time source-frame indexes. getVideoPlaybackQuality counters are retained only as contextual signals and are never subtracted from requestVideoFrameCallback.presentedFrames.",
             "mode_totals":mode_totals,"clips":clip_summaries,"factorial_effects":factorial,"per_clip_factor_effects":effects,"source_videos":source_rows}
    (output/"phase8_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    write_csv(output/"phase8_clip_metrics.csv",clip_summaries)
    write_csv(output/"phase8_frame_metrics.csv",frame_rows)
    write_csv(output/"phase8_event_timeline.csv",event_rows)
    write_csv(output/"phase8_playback_heartbeats.csv",heartbeat_rows)
    write_csv(output/"phase8_factorial_effects.csv",factorial)
    write_csv(output/"phase8_per_clip_factor_effects.csv",effects)
    write_csv(output/"phase8_source_pts_metadata.csv",source_rows)
    report=[
        "# Phase 8 — Callback lifecycle and 2×2 factor-isolation report","",
        "## Scope and guardrails","",
        "Four combinations were tested: 39 ms gate + sync encoder; no gate + sync encoder; 39 ms gate + async encoder; no gate + async encoder.",
        "All use requestVideoFrameCallback, synchronous drawImage, 640 px Canvas, JPEG q92 and the same six source videos. No classifier/checkpoint or production recognition path was loaded. IoT remained disabled.",
        "Capture coverage is calculated from unique FFprobe best_effort_timestamp_time frame indexes / source-frame count.",
        "The browser getVideoPlaybackQuality totalVideoFrames/droppedVideoFrames counters are retained as context only. They are not mixed with metadata.presentedFrames.","",
        "## Weighted source-frame coverage","",
        "| Mode | Captured unique source frames | Source frames | Coverage | Delivered callbacks | Gate skips | Mean callback tail silence |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        t=mode_totals[mode];report.append(f"| {mode} | {t['captured_unique_source_frames']} | {t['source_frames']} | {t['weighted_source_frame_coverage_pct']:.1f}% | {t['rVFC_delivered']} | {t['callback_gate_skips']} | {t['mean_callback_tail_silence_ms']:.1f} ms |")
    report += ["","## Factorial contrasts","","| Contrast | Mean coverage difference | Positive clips | Negative clips |","|---|---:|---:|---:|"]
    for r in factorial: report.append(f"| {r['contrast']} | {r['mean_clip_coverage_difference_pp']:+.1f} pp | {r['clips_positive']}/6 | {r['clips_negative']}/6 |")
    report += ["","## Per-clip coverage and callback tail","","| Clip | Gated sync | Ungated sync | Gated async | Ungated async | Source frames |","|---|---:|---:|---:|---:|---:|"]
    for path in TARGET_PATHS:
        subset={m:by[(path,m)] for m in MODES}; n=subset["gate_sync"]["source_frame_count"]
        report.append(f"| {Path(path).stem} | {subset['gate_sync']['source_frame_coverage_pct']:.1f}% | {subset['ungate_sync']['source_frame_coverage_pct']:.1f}% | {subset['gate_async']['source_frame_coverage_pct']:.1f}% | {subset['ungate_async']['source_frame_coverage_pct']:.1f}% | {n} |")
    report += ["","## Callback blackouts, visibility and encoder queue","",
               "Per-frame callback timestamps, `document.visibilityState`, `document.hasFocus`, video readyState/paused state, video lifecycle events, 100 ms playback heartbeats, callback request/delivery counts, queue depth and encode latency are retained in the CSV outputs.",
               "A long tail silence is computed as capture end time minus the last accepted frame callback elapsed time. This exposes callbacks that stop before the video reaches its ended event.","",
               "## Important interpretation limits","",
               "The four passes are sequential, not simultaneous. The page order is rotated across modes to reduce clip-position bias, but this remains a small six-clip diagnostic and cannot establish production-wide performance.",
               "Do not promote a mode unless it shows reproducible source-frame coverage, no unexplained callback blackouts, bounded encode queue, and acceptable behaviour on repeated runs.","",
               "## Engineering decision","",
               "This is diagnostic-only evidence. It does not authorize changing the production capture loop, model, checkpoint, feature layout, frame length or IoT configuration.",]
    (output/"PHASE8_CALLBACK_LIFECYCLE_REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    return summary


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True);parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--manifest",type=Path,required=True);parser.add_argument("--ffprobe",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    for mode in MODES: parser.add_argument(f"--{mode.replace('_','-')}-dir",dest=mode,type=Path,required=True)
    args=parser.parse_args()
    summary=run(args)
    print("PHASE8_STATUS=completed")
    for mode,values in summary["mode_totals"].items():
        print(f"MODE={mode} | source_coverage={values['weighted_source_frame_coverage_pct']:.1f}% | captured={values['captured_unique_source_frames']}/{values['source_frames']} | callbacks={values['rVFC_delivered']} | tail_silence_mean_ms={values['mean_callback_tail_silence_ms']:.1f}")
    print(f"OUTPUT={args.output_dir.resolve()}")
    print("IoT disabled; classifier/checkpoint not loaded; production code unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
