#!/usr/bin/env python3
"""Phase 10B: independently validate lifecycle exposures and correctly account for censoring."""
from __future__ import annotations
import argparse, bisect, csv, hashlib, json, math, statistics, sys
from collections import Counter
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for p in (ROOT / "src", SCRIPTS):
    if str(p) not in sys.path: sys.path.insert(0, str(p))
from browser_capture_protocol import parse_capture_payload, normalize_video_path
from serve_browser_validation_capture import read_and_verify_manifest
from phase9_repeatability_analysis import ffprobe_pts, locate_source, nearest_index, missing_ranges, percentile

TARGET_PATHS = (
    "Greetings/51. Good Morning/MVI_0099.MOV",
    "Colours/55. White/MVI_5063.MOV",
    "Adjectives/83. big large/MVI_9396.MOV",
    "Adjectives/79. short/MVI_9302.MOV",
    "Means_of_Transportation/11. Car/MVI_3118.MOV",
    "Pronouns/40. I/MVI_9922.MOV",
)
MODES = ("foreground_focused", "visible_unfocused", "hidden_background", "focus_recovery")
THRESHOLDS_MS = {"visible_unfocused": 1500.0, "hidden_background": 1500.0, "focus_recovery": 500.0}
REQUIRED_OUTPUTS = (
    "phase10b_summary.json", "PHASE10B_BROWSER_LIFECYCLE_REPORT.md", "phase10b_mode_metrics.csv",
    "phase10b_clip_metrics.csv", "phase10b_frame_metrics.csv", "phase10b_event_timeline.csv",
    "phase10b_playback_heartbeats.csv", "phase10b_hidden_intervals.csv",
    "phase10b_visible_unfocused_intervals.csv", "phase10b_source_pts_metadata.csv",
)

def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8"); return
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def safe_capture_path(run_dir: Path, relative: str) -> Path:
    rel=Path(relative)
    if rel.is_absolute() or ".." in rel.parts: raise ValueError(f"Unsafe capture path: {relative}")
    resolved=(run_dir/rel).resolve(); resolved.relative_to(run_dir.resolve()); return resolved

def event_epoch(e: dict[str,Any]) -> float:
    value=e.get("wall_time_epoch_ms")
    return float(value) if value is not None else 0.0

def lifecycle_intervals(timeline: list[dict[str,Any]], end_epoch: float) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    """Return hidden and visible-unfocused intervals; unmatched starts are right-censored lower bounds."""
    events=sorted(timeline,key=event_epoch)
    hidden=[]; unfocused=[]; hidden_open=None; blur_open=None
    for e in events:
        kind=e.get("event_type"); at=event_epoch(e)
        if kind=="document_visibilitychange":
            new_state=e.get("visibility_state_after") or e.get("visibility_state")
            if new_state=="hidden" and hidden_open is None:
                hidden_open={"start_epoch_ms":at,"start_elapsed_ms":e.get("elapsed_ms"),
                             "start_video_time_seconds":e.get("video_current_time_seconds"),
                             "started_during_playback":e.get("video_paused") is False}
            elif new_state=="visible" and hidden_open is not None:
                hidden.append({**hidden_open,"end_epoch_ms":at,"end_elapsed_ms":e.get("elapsed_ms"),
                               "duration_ms":max(0.0,at-hidden_open["start_epoch_ms"]),"censored":False,
                               "end_reason":"visibility_return"})
                hidden_open=None
        # A user can blur the window immediately after pressing Capture, before the
        # media element emits `playing`. If that happened while video_paused=True,
        # rebase the measurable interval to video_playing. Pre-play time is retained
        # for audit but must never count toward the >=1.5 s playback exposure.
        if kind=="video_playing" and e.get("visibility_state")=="visible" and e.get("has_focus") is False:
            if blur_open is None:
                blur_open={"start_epoch_ms":at,"start_elapsed_ms":e.get("elapsed_ms"),
                           "start_video_time_seconds":e.get("video_current_time_seconds"),
                           "started_visible":True,"started_during_playback":True,"opened_by":"video_playing_unfocused"}
            elif blur_open.get("started_during_playback") is not True:
                preplay_start=blur_open["start_epoch_ms"]
                blur_open={**blur_open,"preplay_blur_start_epoch_ms":preplay_start,
                           "preplay_blur_duration_ms":max(0.0,at-preplay_start),
                           "start_epoch_ms":at,"start_elapsed_ms":e.get("elapsed_ms"),
                           "start_video_time_seconds":e.get("video_current_time_seconds"),
                           "started_visible":True,"started_during_playback":True,
                           "opened_by":"window_blur_then_video_playing"}
        if kind=="window_blur" and e.get("visibility_state")=="visible" and e.get("has_focus") is False and blur_open is None:
            blur_open={"start_epoch_ms":at,"start_elapsed_ms":e.get("elapsed_ms"),
                       "start_video_time_seconds":e.get("video_current_time_seconds"),
                       "started_visible":True,"started_during_playback":e.get("video_paused") is False,"opened_by":"window_blur"}
        elif kind=="window_focus" and blur_open is not None:
            unfocused.append({**blur_open,"end_epoch_ms":at,"end_elapsed_ms":e.get("elapsed_ms"),
                              "duration_ms":max(0.0,at-blur_open["start_epoch_ms"]),"censored":False,
                              "ended_visible":e.get("visibility_state")=="visible","ended_focused":e.get("has_focus") is True,
                              "end_reason":"window_focus"})
            blur_open=None
    if hidden_open is not None:
        hidden.append({**hidden_open,"end_epoch_ms":end_epoch,"end_elapsed_ms":None,
                       "duration_ms":max(0.0,end_epoch-hidden_open["start_epoch_ms"]),"censored":True,
                       "end_reason":"capture_end_right_censored"})
    if blur_open is not None:
        unfocused.append({**blur_open,"end_epoch_ms":end_epoch,"end_elapsed_ms":None,
                          "duration_ms":max(0.0,end_epoch-blur_open["start_epoch_ms"]),"censored":True,
                          "ended_visible":True,"ended_focused":False,"end_reason":"capture_end_right_censored"})
    return hidden,unfocused

def heartbeat_visible_unfocused_intervals(
    heartbeats: list[dict[str, Any]],
    end_epoch_ms: float,
    final_state: dict[str, Any] | None = None,
    *,
    max_gap_ms: float = 450.0,
    state_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Derive conservative visible/unfocused spans from all 100 ms heartbeats.

    Crucially, inactive samples are processed as interval boundaries; the function
    never filters them away before grouping. This prevents a focus/visibility/playback
    state change from being bridged by two otherwise-active samples. Durations are
    measured only from actual active heartbeat timestamps, so they are conservative
    lower bounds. The final open group is right-censored and is not extended past its
    last actual sample.
    """
    samples = [
        {**item, "_observation_source": "heartbeat"}
        for item in heartbeats if item.get("wall_time_epoch_ms") is not None
    ]
    # Lifecycle events are exact state-transition observations. Insert them into the
    # heartbeat stream so a focus/visibility/pause transition between two 100 ms
    # timer ticks cannot be bridged by the sampled-state interval.
    for event in (state_events or []):
        kind = event.get("event_type")
        if kind not in {"window_blur", "window_focus", "document_visibilitychange", "video_playing", "video_pause", "video_ended_drain_started"}:
            continue
        if event.get("wall_time_epoch_ms") is None:
            continue
        samples.append({
            "wall_time_epoch_ms": event.get("wall_time_epoch_ms"),
            "visibility_state": event.get("visibility_state_after") or event.get("visibility_state"),
            "has_focus": event.get("has_focus"),
            "video_paused": event.get("video_paused"),
            "video_ended": event.get("video_ended") or kind == "video_ended_drain_started",
            "_observation_source": "lifecycle_event",
            "_event_type": kind,
        })
    samples = sorted(
        enumerate(samples),
        key=lambda pair: (float(pair[1]["wall_time_epoch_ms"]), pair[0]),
    )
    samples = [item for _index, item in samples]
    output: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []

    def is_active(item: dict[str, Any]) -> bool:
        return (
            item.get("visibility_state") == "visible"
            and item.get("has_focus") is False
            and item.get("video_paused") is False
            and not bool(item.get("video_ended"))
        )

    def emit(items: list[dict[str, Any]], next_sample: dict[str, Any] | None = None,
             *, right_censored: bool = False) -> None:
        if len(items) < 2:
            return
        start = float(items[0]["wall_time_epoch_ms"])
        last = float(items[-1]["wall_time_epoch_ms"])
        gaps = [float(b["wall_time_epoch_ms"]) - float(a["wall_time_epoch_ms"])
                for a, b in zip(items, items[1:])]
        if any(gap <= 0 or gap > max_gap_ms for gap in gaps):
            return
        duration = max(0.0, last - start)
        if duration <= 0:
            return
        if next_sample is not None:
            ended_visible = next_sample.get("visibility_state") == "visible"
            ended_focused = next_sample.get("has_focus") is True
            end_reason = "heartbeat_observed_state_change_lower_bound"
        else:
            ended_visible = bool(final_state and final_state.get("visibility_state") == "visible")
            ended_focused = bool(final_state and final_state.get("has_focus") is True)
            end_reason = "capture_end_right_censored_heartbeat_lower_bound" if right_censored else "heartbeat_stream_gap_lower_bound"
        output.append({
            "start_epoch_ms": start, "end_epoch_ms": last, "duration_ms": duration,
            "censored": bool(right_censored), "end_reason": end_reason,
            "started_visible": True, "started_during_playback": True,
            "ended_visible": ended_visible, "ended_focused": ended_focused,
            "opened_by": "heartbeat_state_sampling",
            "heartbeat_sample_count": sum(x.get("_observation_source") == "heartbeat" for x in items),
            "lifecycle_event_sample_count": sum(x.get("_observation_source") == "lifecycle_event" for x in items),
            "heartbeat_max_gap_ms": max(gaps, default=0.0),
            "heartbeat_observation_gap_limit_ms": max_gap_ms,
            "duration_semantics": "observed_lower_bound",
        })

    for sample in samples:
        if is_active(sample):
            if group:
                gap = float(sample["wall_time_epoch_ms"]) - float(group[-1]["wall_time_epoch_ms"])
                if gap <= 0 or gap > max_gap_ms:
                    emit(group, right_censored=True)
                    group = [sample]
                else:
                    group.append(sample)
            else:
                group = [sample]
        elif group:
            emit(group, next_sample=sample)
            group = []

    if group:
        end_epoch = float(end_epoch_ms or group[-1]["wall_time_epoch_ms"])
        gap_to_end = max(0.0, end_epoch - float(group[-1]["wall_time_epoch_ms"]))
        # `end_epoch_ms` is the playback end event when one exists. The final
        # pageState is sampled after the short end-drain, so video_paused/video_ended
        # may already be true even though the final active heartbeat was valid.
        # Only require the page to remain visible and unfocused; do not extend the
        # measured duration beyond the last heartbeat sample.
        final_is_active = bool(
            final_state
            and final_state.get("visibility_state") == "visible"
            and final_state.get("has_focus") is False
        )
        emit(group, right_censored=(gap_to_end <= max_gap_ms and final_is_active))
    return output

def hidden_interval_rows(mode: str, case: dict[str,Any], intervals: list[dict[str,Any]]) -> list[dict[str,Any]]:
    return [{"mode":mode,"video_path":case["video_path"],"interval_index":i+1,**v,
             "duration_semantics":"lower_bound_right_censored" if v["censored"] else "complete_observed_duration"}
            for i,v in enumerate(intervals)]

def classify_condition(mode: str, initial: dict[str,Any], frames: list[dict[str,Any]], timeline: list[dict[str,Any]], marker: Any,
                       hidden: list[dict[str,Any]], unfocused: list[dict[str,Any]],
                       heartbeats: list[dict[str, Any]] | None = None, end_epoch_ms: float | None = None,
                       final_state: dict[str, Any] | None = None) -> tuple[bool,str,dict[str,Any]]:
    event_types=[str(e.get("event_type","")) for e in timeline]
    hidden_events=[e for e in timeline if e.get("event_type")=="document_visibilitychange" and
                   (e.get("visibility_state_after") or e.get("visibility_state"))=="hidden"]
    if mode=="foreground_focused":
        all_rows=all(f.get("visibility_state")=="visible" and f.get("has_focus") is True for f in frames)
        ok=(initial.get("visibility_state")=="visible" and initial.get("has_focus") is True and all_rows and
            "window_blur" not in event_types and not hidden_events)
        return ok, ("foreground/focused maintained for all captured frames" if ok else "foreground condition invalidated by blur/hidden/unfocused frame"), {
            "all_captured_frames_visible_focused":all_rows,"blur_events":event_types.count("window_blur"),"hidden_transitions":len(hidden_events)}
    if mode=="visible_unfocused":
        threshold=THRESHOLDS_MS[mode]
        heartbeat_intervals = heartbeat_visible_unfocused_intervals(
            heartbeats or [], float(end_epoch_ms or 0), final_state, state_events=timeline
        )
        candidates = list(unfocused) + heartbeat_intervals
        qualifying=[]
        for iv in candidates:
            hidden_during=any(event_epoch(e)>=iv["start_epoch_ms"] and event_epoch(e)<=iv["end_epoch_ms"] for e in hidden_events)
            if (iv.get("started_visible") and iv.get("started_during_playback") and
                iv["duration_ms"]>=threshold and iv.get("ended_visible") and not hidden_during):
                qualifying.append(iv)
        chosen=max(qualifying,key=lambda x:x["duration_ms"],default=None)
        ok=chosen is not None
        best_observed=max((x["duration_ms"] for x in candidates if x.get("started_visible") and x.get("started_during_playback")),default=0.0)
        reason=(f"visible/unfocused exposure {chosen['duration_ms']:.0f} ms via {chosen.get('opened_by','focus events')}" if ok else
                f"requires visible/unfocused exposure >= {threshold:.0f} ms during playback; best observed={best_observed:.0f} ms; focus-event intervals={len(unfocused)}; heartbeat intervals={len(heartbeat_intervals)}")
        return ok, reason, {"threshold_ms":threshold,"best_observed_ms":best_observed,
                            "qualifying_interval":chosen,"interval_count":len(candidates),
                            "focus_event_intervals":unfocused,"heartbeat_intervals":heartbeat_intervals,
                            "intervals":candidates}
    if mode=="hidden_background":
        threshold=THRESHOLDS_MS[mode]
        chosen=max((x for x in hidden if x.get("started_during_playback") and not x.get("censored") and x["duration_ms"]>=threshold),
                   key=lambda x:x["duration_ms"],default=None)
        ok=chosen is not None
        return ok, (f"hidden exposure {chosen['duration_ms']:.0f} ms closed by visible return" if ok else f"requires a closed hidden exposure >= {threshold:.0f} ms that began during playback"), {
            "threshold_ms":threshold,"require_visible_return":True,"qualifying_interval":chosen,"interval_count":len(hidden),"intervals":hidden}
    threshold=THRESHOLDS_MS[mode]
    chosen=max((x for x in unfocused if x.get("started_visible") and x.get("started_during_playback") and
                x.get("ended_visible") and x.get("ended_focused") and not x["censored"] and x["duration_ms"]>=threshold),
               key=lambda x:x["duration_ms"],default=None)
    marker_ok=bool(chosen and isinstance(marker,dict) and marker.get("trusted") is True and
                   marker.get("visibility_state")=="visible" and marker.get("has_focus") is True and
                   float(marker.get("wall_time_epoch_ms") or 0)>=float(chosen["end_epoch_ms"]))
    ok=bool(chosen and marker_ok)
    return ok, (f"blur/focus recovery {chosen['duration_ms']:.0f} ms with trusted return click" if ok else f"requires blur/focus >= {threshold:.0f} ms and trusted visible/focused click after focus returns"), {
        "threshold_ms":threshold,"qualifying_interval":chosen,"interval_count":len(unfocused),"intervals":unfocused,"marker":marker,"marker_valid":marker_ok}

def focus_state_counts(frames: list[dict[str,Any]]) -> dict[str,int]:
    cb=[f for f in frames if f.get("capture_kind")=="rvfc_playback"]
    vf=sum(f.get("visibility_state")=="visible" and f.get("has_focus") is True for f in cb)
    vu=sum(f.get("visibility_state")=="visible" and f.get("has_focus") is False for f in cb)
    hid=sum(f.get("visibility_state")=="hidden" for f in cb)
    return {"callback_frames":len(cb),"callbacks_visible_focused":vf,"callbacks_visible_unfocused":vu,"callbacks_hidden":hid,
            "callbacks_other_or_unknown":len(cb)-vf-vu-hid}

def load_capture(run_dir: Path, expected: dict[str,str], mode: str) -> tuple[dict[str,Any],tuple[bytes,...]]:
    index=json.loads((run_dir/"capture_index.json").read_text(encoding="utf-8"))
    if index.get("official_split_count")!=77 or index.get("received_count")!=6: raise ValueError(f"{mode}: index must show 6 captures from 77-row official manifest")
    cfg=index.get("capture_config",{})
    if (cfg.get("phase10b_mode"),cfg.get("phase10b_run_id"))!=(mode,mode): raise ValueError(f"{mode}: index identity mismatch")
    want=normalize_video_path(expected["video_path"]).casefold()
    record=next((x for x in index.get("received",[]) if normalize_video_path(x["video_path"]).casefold()==want),None)
    if record is None: raise FileNotFoundError(f"{mode}: missing {expected['video_path']}")
    rel=Path(record.get("container",""))
    if rel.is_absolute() or ".." in rel.parts: raise ValueError("Unsafe capture container path")
    container=(run_dir/rel).resolve(); container.relative_to(run_dir.resolve())
    parsed=parse_capture_payload(container.read_bytes(),expected)
    if parsed.sha256!=record.get("sha256"): raise ValueError(f"{mode}: capture SHA mismatch for {expected['video_path']}")
    case=parsed.header["case"]; config=parsed.header["capture_config"]
    for obj,label in ((case,"case"),(config,"config")):
        if (obj.get("phase10b_mode"),obj.get("phase10b_run_id"),obj.get("phase10b_capture_encoder"))!=(mode,mode,"sync"):
            raise ValueError(f"{mode}: {label} identity/encoder mismatch")
    if case.get("phase10b_condition_validation",{}).get("valid") is not True:
        raise ValueError(f"{mode}: unverified clip was uploaded; receiver/browser enforcement contract violated")
    if case.get("phase10b_attempt_number") not in range(1,6): raise ValueError("Invalid Phase 10B attempt number")
    if not case.get("timeline_events") or not case.get("frames"): raise ValueError("Capture missing timeline/frame rows")
    return case,parsed.frames

def summarize_pts_frames(pts: list[float], frames: list[dict[str,Any]]) -> dict[str,Any]:
    if len(pts) < 2:
        raise ValueError("At least two FFprobe PTS values are required for frame-alignment validation")
    timestamps = [float(frame["video_time_seconds"]) for frame in frames]
    mappings = [nearest_index(pts, timestamp) for timestamp in timestamps]
    indexes = [i for i, _ in mappings]
    unique = set(indexes)
    initial = [i for i, frame in enumerate(frames) if frame.get("capture_kind") == "initial_paused"]
    missing = sorted(set(range(len(pts))) - unique)
    interior = [i for i in missing if i not in {0, len(pts) - 1}]
    callback_frames = [frame for frame in frames if frame.get("capture_kind") == "rvfc_playback"]
    presented = [int(frame["presented_frames"]) for frame in callback_frames if frame.get("presented_frames") is not None]
    presented_gaps = [max(0, b - a - 1) for a, b in zip(presented, presented[1:])]
    delta_ms = [abs(delta) * 1000 for _, delta in mappings]
    tolerances_ms = []
    alignment_violations = 0
    for timestamp, (index, delta) in zip(timestamps, mappings):
        if delta < 0 and index > 0:
            local_step = pts[index] - pts[index - 1]
        elif index + 1 < len(pts):
            local_step = pts[index + 1] - pts[index]
        else:
            local_step = pts[index] - pts[index - 1]
        tolerance_ms = (0.5 * local_step + 0.002) * 1000
        tolerances_ms.append(tolerance_ms)
        if abs(delta) * 1000 > tolerance_ms:
            alignment_violations += 1
    timestamps_strictly_increasing = all(b > a for a, b in zip(timestamps, timestamps[1:]))
    mapped_indexes_strictly_increasing = all(b > a for a, b in zip(indexes, indexes[1:]))
    initial_contract_valid = (
        len(initial) == 1 and bool(frames) and frames[0].get("capture_kind") == "initial_paused"
        and abs(float(frames[0].get("video_time_seconds", -1))) <= 0.0001 and indexes[0] == 0
    )
    alignment_valid = (
        alignment_violations == 0 and timestamps_strictly_increasing and mapped_indexes_strictly_increasing
        and initial_contract_valid
    )
    return {
        "source_frame_count": len(pts), "captured_frame_count": len(frames),
        "unique_source_frames_captured": len(unique),
        "source_frame_coverage_pct": round(100 * len(unique) / max(1, len(pts)), 3),
        "duplicate_source_frame_matches": len(indexes) - len(unique),
        "missing_source_frame_count": len(missing), "missing_interior_frame_count": len(interior),
        "missing_source_frame_ranges": missing_ranges(unique, len(pts)),
        "initial_paused_records": len(initial),
        "first_record_is_initial_paused": bool(frames and frames[0].get("capture_kind") == "initial_paused"),
        "initial_frame_maps_to_source_zero": bool(initial and indexes[initial[0]] == 0),
        "initial_frame_contract_valid": initial_contract_valid,
        "capture_timestamps_strictly_increasing": timestamps_strictly_increasing,
        "mapped_source_indexes_strictly_increasing": mapped_indexes_strictly_increasing,
        "pts_alignment_valid": alignment_valid,
        "pts_alignment_violation_count": alignment_violations,
        "pts_alignment_tolerance_max_ms": max(tolerances_ms, default=None),
        "callback_frames": len(callback_frames),
        "presented_frame_gap_total": sum(presented_gaps),
        "presented_frame_gap_max": max(presented_gaps) if presented_gaps else 0,
        "pts_abs_delta_p95_ms": percentile(delta_ms, .95),
        "pts_abs_delta_max_ms": max(delta_ms, default=None),
        "mapped_source_indexes": indexes,
    }

def analyze_clip(mode: str, case: dict[str,Any], images: tuple[bytes,...], pts: list[float], pts_meta: dict[str,Any]) -> tuple[dict[str,Any],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]]]:
    frames=case["frames"]; timeline=case.get("timeline_events",[]); heartbeats=case.get("heartbeat_samples",[])
    end_epoch=float(case.get("capture_finish_wall_time_epoch_ms") or max((event_epoch(e) for e in timeline),default=0))
    playback_end_event=next((e for e in reversed(timeline) if e.get("event_type")=="video_ended_drain_started"),None)
    # Hidden-tab exposure remains observable after media playback ends. The capture
    # page deliberately waits for a visible return so the interval can be closed.
    exposure_end_epoch=end_epoch if mode=="hidden_background" else (event_epoch(playback_end_event) if playback_end_event else end_epoch)
    hidden,unfocused=lifecycle_intervals(timeline,end_epoch)
    frame_summary=summarize_pts_frames(pts,frames)
    focus_counts=focus_state_counts(frames)
    initial=frames[0] if frames else {}
    marker=case.get("focus_recovery_marker")
    final_state = {"visibility_state":case.get("page_visibility_at_finish"),
                   "has_focus":case.get("page_has_focus_at_finish"),
                   "video_ended":case.get("video_ended_at_finish"),
                   "video_paused":case.get("video_paused_at_finish")}
    py_valid,py_note,py_evidence=classify_condition(
        mode,initial,frames,timeline,marker,hidden,unfocused,heartbeats,exposure_end_epoch,final_state
    )
    client=case.get("phase10b_condition_validation") or {}
    client_valid=client.get("valid") is True
    finish_events=[e for e in timeline if e.get("event_type")=="capture_finished"]
    hidden_complete=[x for x in hidden if not x["censored"]]
    hidden_censored=[x for x in hidden if x["censored"]]
    focus_complete=[x for x in unfocused if not x["censored"]]
    focus_censored=[x for x in unfocused if x["censored"]]
    event_types=[str(e.get("event_type","")) for e in timeline]
    heartbeat_times=[float(x["elapsed_ms"]) for x in heartbeats if x.get("elapsed_ms") is not None]
    hb_gaps=[b-a for a,b in zip(heartbeat_times,heartbeat_times[1:]) if b>=a]
    cb_times=[float(f["callback_time_ms"]) for f in frames if f.get("callback_time_ms") is not None]
    cb_gaps=[b-a for a,b in zip(cb_times,cb_times[1:]) if b>=a]
    frame_rows=[]
    for i,(frame,jpeg) in enumerate(zip(frames,images)):
        source_idx,delta=nearest_index(pts,float(frame["video_time_seconds"]))
        if delta<0 and source_idx>0: local_pts_step=pts[source_idx]-pts[source_idx-1]
        elif source_idx+1<len(pts): local_pts_step=pts[source_idx+1]-pts[source_idx]
        else: local_pts_step=pts[source_idx]-pts[source_idx-1]
        pts_tolerance_ms=(0.5*local_pts_step+0.002)*1000
        frame_rows.append({"mode":mode,"video_path":case["video_path"],"label":case["label"],"capture_frame_index":i,
            "capture_kind":frame.get("capture_kind"),"source_pts_frame_index":source_idx,"source_pts_seconds":pts[source_idx],
            "capture_minus_pts_ms":round(delta*1000,6),"pts_tolerance_ms":round(pts_tolerance_ms,6),
            "pts_within_half_frame_tolerance":abs(delta)*1000<=pts_tolerance_ms,"presented_frames":frame.get("presented_frames"),
            "callback_time_ms":frame.get("callback_time_ms"),"elapsed_ms":frame.get("elapsed_ms"),
            "wall_time_epoch_ms":frame.get("wall_time_epoch_ms"),"visibility_state":frame.get("visibility_state"),
            "has_focus":frame.get("has_focus"),"callback_work_ms":frame.get("callback_work_ms"),
            "encode_latency_ms":frame.get("encode_latency_ms"),"jpeg_bytes":len(jpeg),"jpeg_sha256":hashlib.sha256(jpeg).hexdigest()})
    timeline_rows=[{"mode":mode,"video_path":case["video_path"],**event} for event in timeline]
    heartbeat_rows=[{"mode":mode,"video_path":case["video_path"],**item} for item in heartbeats]
    hidden_rows=[{"mode":mode,"video_path":case["video_path"],"interval_index":i+1,**item,
                  "duration_semantics":"right_censored_lower_bound" if item["censored"] else "complete_observed_duration"} for i,item in enumerate(hidden)]
    unfocused_rows=[{"mode":mode,"video_path":case["video_path"],"interval_index":i+1,**item,
                     "duration_semantics":"right_censored_lower_bound" if item["censored"] else "complete_observed_duration"} for i,item in enumerate(unfocused)]
    row={"mode":mode,"video_path":case["video_path"],"label":case["label"],**{k:v for k,v in frame_summary.items() if k!="mapped_source_indexes"},**focus_counts,
         "phase10b_attempt_number":case.get("phase10b_attempt_number"),"phase10b_prior_retry_count":len(case.get("phase10b_prior_attempt_failures") or []),
         "client_condition_valid":client_valid,"python_condition_valid":py_valid,"condition_validation_agreement":client_valid==py_valid,
         "mode_exposure_confirmed":py_valid,"mode_exposure_note":py_note,"mode_exposure_evidence_json":json.dumps(py_evidence,separators=(",",":")),
         "window_blur_events":event_types.count("window_blur"),"window_focus_events":event_types.count("window_focus"),
         "hidden_visibility_transitions":sum(1 for e in timeline if e.get("event_type")=="document_visibilitychange" and (e.get("visibility_state_after") or e.get("visibility_state"))=="hidden"),
         "hidden_interval_count":len(hidden),"hidden_complete_interval_count":len(hidden_complete),"hidden_right_censored_interval_count":len(hidden_censored),
         "hidden_duration_complete_max_ms":max((x["duration_ms"] for x in hidden_complete),default=None),
         "hidden_right_censored_lower_bound_max_ms":max((x["duration_ms"] for x in hidden_censored),default=None),
         "visible_unfocused_interval_count":len(unfocused),"visible_unfocused_heartbeat_interval_count":len(py_evidence.get("heartbeat_intervals",[])),
         "visible_unfocused_heartbeat_sample_count":sum(int(x.get("heartbeat_sample_count",0)) for x in py_evidence.get("heartbeat_intervals",[])),
         "visible_unfocused_complete_interval_count":len(focus_complete),
         "visible_unfocused_right_censored_interval_count":len(focus_censored),
         "visible_unfocused_complete_max_ms":max((x["duration_ms"] for x in focus_complete),default=None),
         "visible_unfocused_right_censored_lower_bound_max_ms":max((x["duration_ms"] for x in focus_censored),default=None),
         "recovery_marker_trusted":bool(isinstance(marker,dict) and marker.get("trusted") is True),
         "recovery_marker_visible_focused":bool(isinstance(marker,dict) and marker.get("visibility_state")=="visible" and marker.get("has_focus") is True),
         "recovery_marker_after_focus":bool(py_evidence.get("marker_valid",False)),
         "rvfc_silence_events":event_types.count("rvfc_silence_detected"),
         "pagehide_events":event_types.count("pagehide"),"pageshow_events":event_types.count("pageshow"),
         "page_freeze_events":event_types.count("page_freeze"),"page_resume_events":event_types.count("page_resume"),
         "browser_dropped_video_frames":int(case.get("browser_dropped_video_frames",0) or 0),
         "browser_total_video_frames":int(case.get("browser_total_video_frames",0) or 0),
         "video_waiting_events":event_types.count("video_waiting"),"video_stalled_events":event_types.count("video_stalled"),
         "video_ended_at_finish":bool(case.get("video_ended_at_finish")),"page_visibility_at_finish":case.get("page_visibility_at_finish"),
         "page_has_focus_at_finish":case.get("page_has_focus_at_finish"),"page_was_discarded":bool(case.get("page_was_discarded")),
         "callback_gap_max_ms":max(cb_gaps,default=None),"heartbeat_gap_max_ms":max(hb_gaps,default=None),
         "source_video_sha256":pts_meta["source_video_sha256"],"source_pts_median_step_ms":pts_meta["pts_step_median_ms"]}
    return row,frame_rows,timeline_rows,heartbeat_rows,hidden_rows,unfocused_rows

def report_text(mode_rows: list[dict[str,Any]],clip_rows:list[dict[str,Any]],summary:dict[str,Any],pts_rows:list[dict[str,Any]])->str:
    out=["# Phase 10B — Verified lifecycle exposure and censoring", "", "## Executive summary", "",
         f"- Capture status: **{summary['status']}**; modes: **{summary['mode_count']}/4**; accepted clips: **{summary['clip_count']}/24**.",
         f"- Python independently verified **{summary['verified_clip_count']}/24** lifecycle exposures; condition validation disagreements: **{summary['condition_validation_disagreement_count']}**.",
         f"- Unique source-frame coverage: **{summary['weighted_source_coverage_pct']:.2f}%** across **{summary['source_frame_opportunities']}** FFprobe frames.",
         f"- Strict half-frame PTS alignment: **{summary['pts_aligned_clip_count']}/24 clips**; violations: **{summary['pts_alignment_violation_count']}**; initial-frame contracts: **{summary['initial_frame_contract_valid_count']}/24**.",
         "- Each clip was checked in the browser before upload. Failed condition attempts were not uploaded; the successful payload records the attempt number and prior failure reasons.",
         "- All lifecycle durations use wall-clock epoch timestamps. An interval without a matching close event is reported as right-censored, with a lower bound; it is never reported as zero or as a completed duration.",
         "- Canvas 640 px, JPEG q92, synchronous toDataURL, ungated rVFC and paused frame-zero capture were held constant.", "",
         "## Condition acceptance by mode", "",
         "| Mode | Accepted clips | Verified clips | Coverage | Hidden complete intervals | Hidden right-censored intervals | Longest censored lower bound | Exposure complete |",
         "|---|---:|---:|---:|---:|---:|---:|---|"]
    for m in mode_rows:
        mx=m["hidden_right_censored_lower_bound_max_ms"]
        out.append(f"| {m['mode']} | {m['clip_count']} | {m['verified_clip_count']} | {m['source_frame_coverage_pct']:.2f}% | {m['hidden_complete_interval_count']} | {m['hidden_right_censored_interval_count']} | {'NA' if mx is None else f'{mx:.0f} ms'} | {'Yes' if m['mode_exposure_confirmed'] else 'No'} |")
    out += ["", "## Per-clip acceptance, retries and frame coverage", "",
            "| Mode | Clip | Attempt | Failed retries | Verified | Coverage | Interior gaps | Hidden complete | Hidden censored lower bound | Visible-unfocused complete | Visible-unfocused censored lower bound |",
            "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for r in clip_rows:
        hc=r["hidden_duration_complete_max_ms"]; hz=r["hidden_right_censored_lower_bound_max_ms"]
        uc=r["visible_unfocused_complete_max_ms"]; uz=r["visible_unfocused_right_censored_lower_bound_max_ms"]
        fmt=lambda x: "NA" if x is None else f"{x:.0f}"
        out.append(f"| {r['mode']} | {r['label']} | {r['phase10b_attempt_number']} | {r['phase10b_prior_retry_count']} | {'Yes' if r['python_condition_valid'] else 'No'} | {r['source_frame_coverage_pct']:.1f}% | {r['missing_interior_frame_count']} | {fmt(hc)} ms | {fmt(hz)} ms | {fmt(uc)} ms | {fmt(uz)} ms |")
    out += ["", "## Condition contract", "",
            "- **foreground_focused:** initial frame and every captured frame must be visible and focused; any blur or hidden transition invalidates the clip.",
            "- **visible_unfocused:** a visible/unfocused interval of at least 1,500 ms must occur during playback. A clip that ends while still visible/unfocused may satisfy the minimum as a right-censored lower bound if no hidden transition intervenes.",
            "- **hidden_background:** a hidden interval of at least 1,500 ms must begin while the video is playing and close with a visible return. The capture remains active after video end while the tab is hidden so the actual interval can be measured; an unclosed interval is retained as right-censored evidence but is not accepted.",
            "- **focus_recovery:** a visible blur/focus interval of at least 500 ms must close with visible/focused state, followed by a trusted click while visible and focused.",
            "- Invalid condition attempts are not sent to the receiver. The browser allows up to five attempts per clip and records failed-attempt reasons in the accepted capture.",
            "- An unclosed interval's duration is **not** a completed hidden/focus-loss duration. The report labels it as a lower bound and retains censoring metadata.", "",
            "## FFprobe source provenance", "", "| Clip | Source frames | Median PTS step | Source SHA-256 |", "|---|---:|---:|---|"]
    for p in pts_rows: out.append(f"| {p['video_path']} | {p['source_frame_count']} | {p['pts_step_median_ms']:.3f} ms | `{p['source_video_sha256']}` |")
    out += ["", "## Guardrails", "", "No classifier/checkpoint was loaded. IoT remained disabled. Production recognition code and settings were not modified.", ""]
    return "\n".join(out)

def analyze(args:argparse.Namespace)->int:
    repo=args.repo.resolve(); data_root=args.data_root.resolve(); manifest=args.manifest.resolve(); ffprobe=args.ffprobe.resolve(); output=args.output_dir.resolve()
    official=read_and_verify_manifest(manifest,repo,ROOT/"config"/"label_map_include50.json")
    if len(official)!=77: raise ValueError(f"Pinned official validation manifest must have 77 rows; found {len(official)}")
    by_path={normalize_video_path(r["video_path"]).casefold():r for r in official}
    source_cache={}; clip_rows=[]; frame_rows=[]; event_rows=[]; heartbeat_rows=[]; hidden_rows=[]; unfocused_rows=[]; pts_rows=[]
    for mode in MODES:
        run_dir=getattr(args,mode+"_dir").resolve()
        index=json.loads((run_dir/"capture_index.json").read_text(encoding="utf-8"))
        if index.get("received_count")!=6: raise ValueError(f"{mode}: expected 6 accepted clips")
        for video_path in TARGET_PATHS:
            key=normalize_video_path(video_path).casefold(); expected=by_path.get(key)
            if expected is None: raise ValueError(f"Target absent from official manifest: {video_path}")
            case,images=load_capture(run_dir,expected,mode)
            if key not in source_cache:
                source=locate_source(data_root,video_path); pts,meta=ffprobe_pts(ffprobe,source)
                source_cache[key]=(pts,meta,source); pts_rows.append({"video_path":video_path,"source_path":str(source),**meta})
            pts,meta,_source=source_cache[key]
            row,fr,ev,hb,hi,ui=analyze_clip(mode,case,images,pts,meta)
            clip_rows.append(row); frame_rows.extend(fr); event_rows.extend(ev); heartbeat_rows.extend(hb); hidden_rows.extend(hi); unfocused_rows.extend(ui)
    mode_rows=[]
    for mode in MODES:
        group=[r for r in clip_rows if r["mode"]==mode]
        src=sum(r["source_frame_count"] for r in group); uniq=sum(r["unique_source_frames_captured"] for r in group)
        hidden_complete=[r for r in hidden_rows if r["mode"]==mode and not r["censored"]]
        hidden_censored=[r for r in hidden_rows if r["mode"]==mode and r["censored"]]
        mode_rows.append({"mode":mode,"clip_count":len(group),"verified_clip_count":sum(bool(r["python_condition_valid"]) for r in group),
            "source_frame_opportunities":src,"unique_source_frames_captured":uniq,"source_frame_coverage_pct":round(100*uniq/max(1,src),3),
            "hidden_complete_interval_count":len(hidden_complete),"hidden_right_censored_interval_count":len(hidden_censored),
            "hidden_duration_complete_max_ms":max((r["duration_ms"] for r in hidden_complete),default=None),
            "hidden_right_censored_lower_bound_max_ms":max((r["duration_ms"] for r in hidden_censored),default=None),
            "mode_exposure_confirmed":len(group)==6 and all(r["python_condition_valid"] for r in group),
            "failed_attempts_before_acceptance":sum(r["phase10b_prior_retry_count"] for r in group),
            "condition_validation_disagreements":sum(not r["condition_validation_agreement"] for r in group),
            "window_blur_events":sum(r["window_blur_events"] for r in group),"hidden_transitions":sum(r["hidden_visibility_transitions"] for r in group),
            "max_callback_gap_ms":max((r["callback_gap_max_ms"] or 0 for r in group),default=0),
            "max_heartbeat_gap_ms":max((r["heartbeat_gap_max_ms"] or 0 for r in group),default=0)})
    source_total=sum(r["source_frame_count"] for r in clip_rows); unique_total=sum(r["unique_source_frames_captured"] for r in clip_rows)
    verified=sum(bool(r["python_condition_valid"]) for r in clip_rows); disagreements=sum(not r["condition_validation_agreement"] for r in clip_rows)
    pts_aligned=sum(bool(r["pts_alignment_valid"]) for r in clip_rows)
    summary={"status":"completed","mode_count":len(mode_rows),"clip_count":len(clip_rows),"verified_clip_count":verified,
        "verified_mode_count":sum(bool(r["mode_exposure_confirmed"]) for r in mode_rows),"source_frame_opportunities":source_total,
        "unique_source_frames_captured":unique_total,"weighted_source_coverage_pct":round(100*unique_total/max(1,source_total),3),
        "pts_aligned_clip_count":pts_aligned,"all_pts_alignment_valid":pts_aligned==24,
        "pts_alignment_violation_count":sum(int(r["pts_alignment_violation_count"]) for r in clip_rows),
        "initial_frame_contract_valid_count":sum(bool(r["initial_frame_contract_valid"]) for r in clip_rows),
        "condition_validation_disagreement_count":disagreements,
        "all_conditions_verified":verified==24 and disagreements==0 and len(mode_rows)==4 and all(r["mode_exposure_confirmed"] for r in mode_rows),
        "ffprobe_source":"ffprobe_best_effort_timestamp_time","ffprobe_clip_count":len(pts_rows),"iot_enabled":False,
        "classifier_checkpoint_loaded":False,"production_code_modified":False,
        "condition_thresholds_ms":THRESHOLDS_MS,"hidden_intervals_unclosed_are_right_censored":True,"modes":mode_rows}
    output.mkdir(parents=True,exist_ok=True)
    (output/"phase10b_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    write_csv(output/"phase10b_mode_metrics.csv",mode_rows); write_csv(output/"phase10b_clip_metrics.csv",clip_rows)
    write_csv(output/"phase10b_frame_metrics.csv",frame_rows); write_csv(output/"phase10b_event_timeline.csv",event_rows)
    write_csv(output/"phase10b_playback_heartbeats.csv",heartbeat_rows); write_csv(output/"phase10b_hidden_intervals.csv",hidden_rows)
    write_csv(output/"phase10b_visible_unfocused_intervals.csv",unfocused_rows); write_csv(output/"phase10b_source_pts_metadata.csv",pts_rows)
    (output/"PHASE10B_BROWSER_LIFECYCLE_REPORT.md").write_text(report_text(mode_rows,clip_rows,summary,pts_rows),encoding="utf-8")
    print(f"PHASE10B_STATUS=completed | verified_clips={verified}/24 | verified_modes={summary['verified_mode_count']}/4 | coverage={summary['weighted_source_coverage_pct']:.2f}%",flush=True)
    for m in mode_rows: print(f"MODE={m['mode']} | verified={m['verified_clip_count']}/6 | coverage={m['source_frame_coverage_pct']:.2f}% | hidden_complete={m['hidden_complete_interval_count']} | hidden_right_censored={m['hidden_right_censored_interval_count']} | exposure={m['mode_exposure_confirmed']}",flush=True)
    return 0

def main()->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--repo',type=Path,required=True); p.add_argument('--data-root',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True); p.add_argument('--ffprobe',type=Path,required=True); p.add_argument('--output-dir',type=Path,required=True)
    for mode in MODES: p.add_argument(f"--{mode.replace('_','-')}-dir",dest=mode+'_dir',type=Path,required=True)
    a=p.parse_args()
    try: return analyze(a)
    except Exception as exc: print(f"PHASE10B_ANALYSIS_BLOCKED: {type(exc).__name__}: {exc}",file=sys.stderr,flush=True); return 2
if __name__=='__main__': raise SystemExit(main())
