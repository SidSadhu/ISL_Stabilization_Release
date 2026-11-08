from __future__ import annotations
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _e(kind, t, **kw):
    return {"event_type": kind, "wall_time_epoch_ms": t, "elapsed_ms": t, **kw}


def test_hidden_interval_is_complete_when_visible_transition_closes_it():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals
    timeline = [
        _e("document_visibilitychange", 1000, visibility_state_after="hidden", video_paused=False,
           video_current_time_seconds=0.8),
        _e("document_visibilitychange", 3100, visibility_state_after="visible", video_paused=False,
           video_current_time_seconds=2.9),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 5000)
    assert len(hidden) == 1 and not hidden[0]["censored"]
    assert hidden[0]["duration_ms"] == 2100
    assert hidden[0]["end_reason"] == "visibility_return"
    assert unfocused == []


def test_hidden_interval_without_return_is_right_censored_lower_bound():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals
    timeline = [_e("document_visibilitychange", 1000, visibility_state_after="hidden", video_paused=False,
                   video_current_time_seconds=0.8)]
    hidden, _ = lifecycle_intervals(timeline, 3600)
    assert len(hidden) == 1 and hidden[0]["censored"]
    assert hidden[0]["duration_ms"] == 2600
    assert hidden[0]["end_reason"] == "capture_end_right_censored"


def test_visible_unfocused_can_start_when_next_clip_begins_already_blurred():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [_e("video_playing", 1000, visibility_state="visible", has_focus=False,
                   video_paused=False, video_current_time_seconds=0.0),
                _e("capture_finished", 2800, visibility_state="visible", has_focus=False,
                   video_paused=False, video_current_time_seconds=1.8)]
    hidden, unfocused = lifecycle_intervals(timeline, 2800)
    initial = {"visibility_state": "visible", "has_focus": False}
    frames = [{"capture_kind": "initial_paused", **initial}]
    ok, note, evidence = classify_condition("visible_unfocused", initial, frames, timeline, None, hidden, unfocused)
    assert ok
    assert "1800 ms" in note
    assert evidence["qualifying_interval"]["censored"] is True


def test_visible_unfocused_requires_minimum_interval_and_no_hidden_overlap():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [
        _e("window_blur", 1000, visibility_state="visible", has_focus=False, video_paused=False, video_current_time_seconds=.2),
        _e("document_visibilitychange", 1800, visibility_state_after="hidden", video_paused=False, video_current_time_seconds=1.0),
        _e("window_focus", 3000, visibility_state="visible", has_focus=True, video_paused=False, video_current_time_seconds=2.2),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 3500)
    ok, _, _ = classify_condition("visible_unfocused", {"visibility_state":"visible","has_focus":True}, [], timeline, None, hidden, unfocused)
    assert not ok


def test_focus_recovery_requires_trusted_marker_after_focus_and_minimum_blur():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [
        _e("window_blur", 1000, visibility_state="visible", has_focus=False, video_paused=False, video_current_time_seconds=.3),
        _e("window_focus", 1900, visibility_state="visible", has_focus=True, video_paused=False, video_current_time_seconds=1.2),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 2400)
    initial={"visibility_state":"visible","has_focus":True}
    trusted={"trusted":True,"visibility_state":"visible","has_focus":True,"wall_time_epoch_ms":1950}
    assert classify_condition("focus_recovery",initial,[],timeline,trusted,hidden,unfocused)[0]
    early={**trusted,"wall_time_epoch_ms":1800}
    assert not classify_condition("focus_recovery",initial,[],timeline,early,hidden,unfocused)[0]
    untrusted={**trusted,"trusted":False}
    assert not classify_condition("focus_recovery",initial,[],timeline,untrusted,hidden,unfocused)[0]


def test_foreground_control_rejects_any_blur_or_hidden_sample():
    from scripts.phase10b_browser_lifecycle_analysis import classify_condition
    initial={"visibility_state":"visible","has_focus":True}
    frames=[{"visibility_state":"visible","has_focus":True},{"visibility_state":"visible","has_focus":False}]
    timeline=[_e("window_blur",100,visibility_state="visible",has_focus=False)]
    assert not classify_condition("foreground_focused",initial,frames,timeline,None,[],[])[0]


def test_phase10b_html_has_condition_enforcement_and_retry_loop():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert "const MODE = __PHASE10B_MODE__;" in html
    assert "const RUN_ID = __PHASE10B_RUN_ID__;" in html
    assert "MAX_ATTEMPTS_PER_CLIP = 5" in html
    assert "assessCondition(MODE" in html
    assert "Retry this clip" in html
    assert "wall_time_epoch_ms: Date.now()" in html
    assert "right-censored" in html or "right-censored" in (ROOT/"PHASE10B_README.md").read_text(encoding="utf-8")
    assert "phase10b_condition_validation" in html


def test_phase10b_server_is_loopback_and_rejects_client_unverified_uploads():
    source=(SCRIPTS/"serve_phase10b_capture.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", args.port)' in source
    assert 'validation.get("valid") is not True' in source
    assert 'phase10b_run_identity_mismatch' in source


def test_phase10b_hidden_interval_csvs_are_required_in_bundle():
    source=(SCRIPTS/"phase10b_browser_lifecycle_orchestrator.py").read_text(encoding="utf-8")
    assert '"phase10b_hidden_intervals.csv"' in source
    assert '"phase10b_visible_unfocused_intervals.csv"' in source


def test_phase10b_analysis_handles_open_intervals_as_censored_not_zero():
    source=(SCRIPTS/"phase10b_browser_lifecycle_analysis.py").read_text(encoding="utf-8")
    assert 'capture_end_right_censored' in source
    assert 'hidden_right_censored_lower_bound_max_ms' in source
    assert 'hidden_duration_complete_max_ms' in source


def test_phase10b_sources_compile():
    names = ("serve_phase10b_capture.py", "phase10b_browser_lifecycle_analysis.py", "phase10b_browser_lifecycle_orchestrator.py")
    for name in names:
        ast.parse((SCRIPTS/name).read_text(encoding="utf-8"), filename=name)
    # The bundle verifier is supplied by the previous Phase 10B overlay and is
    # deliberately not replaced by this diagnostic-only lifecycle patch.
    verifier = SCRIPTS / "verify_phase10b_bundle.py"
    if verifier.exists():
        ast.parse(verifier.read_text(encoding="utf-8"), filename=verifier.name)


def test_phase10b_launcher_uses_a_small_windows_powershell_wrapper():
    source=(SCRIPTS/"run_phase10b_browser_lifecycle.ps1").read_text(encoding="utf-8")
    assert "phase10b_browser_lifecycle_orchestrator.py" in source
    assert "$ErrorActionPreference = 'Continue'" in source
    assert "Start-Process" not in source
    assert "exit 0" not in source.lower() and "exit 2" not in source.lower()
    assert "$global:LASTEXITCODE = 0" in source


def test_phase10b_orchestrator_has_four_passes_and_no_iot_enable_path():
    from scripts.phase10b_browser_lifecycle_orchestrator import PASS_SPECS, MODES
    assert len(PASS_SPECS)==4 and MODES==tuple(m for m,_ in PASS_SPECS)
    source=(SCRIPTS/"phase10b_browser_lifecycle_orchestrator.py").read_text(encoding="utf-8")
    assert 'env["ISL_IOT_ENABLED"] = "false"' in source
    assert 'pre_iot_lockdown' in source


def test_js_visible_unfocused_validation_checks_hidden_overlap_per_interval():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert "hiddenDuringThisInterval" in html
    assert "item.ended_focused && !item.censored" in html


def test_pts_alignment_uses_half_of_the_adjacent_source_interval():
    from scripts.phase10b_browser_lifecycle_analysis import summarize_pts_frames
    pts=[0.0,0.04,0.14,0.18]
    frames=[
        {"capture_kind":"initial_paused","video_time_seconds":0.0},
        {"capture_kind":"rvfc_playback","video_time_seconds":0.14,"presented_frames":3},
    ]
    summary=summarize_pts_frames(pts,frames)
    assert summary["pts_alignment_valid"] is True
    assert summary["initial_frame_contract_valid"] is True
    assert summary["pts_alignment_violation_count"] == 0


def test_pts_alignment_marks_timestamp_outside_half_frame_tolerance():
    from scripts.phase10b_browser_lifecycle_analysis import summarize_pts_frames
    pts=[0.0,0.04,0.14,0.18]
    frames=[
        {"capture_kind":"initial_paused","video_time_seconds":0.0},
        {"capture_kind":"rvfc_playback","video_time_seconds":0.25,"presented_frames":3},
    ]
    summary=summarize_pts_frames(pts,frames)
    assert summary["pts_alignment_valid"] is False
    assert summary["pts_alignment_violation_count"] == 1


def test_pts_alignment_rejects_duplicate_or_nonmonotonic_source_matches():
    from scripts.phase10b_browser_lifecycle_analysis import summarize_pts_frames
    pts=[0.0,0.04,0.08,0.12]
    frames=[
        {"capture_kind":"initial_paused","video_time_seconds":0.0},
        {"capture_kind":"rvfc_playback","video_time_seconds":0.039,"presented_frames":2},
        {"capture_kind":"rvfc_playback","video_time_seconds":0.04,"presented_frames":3},
    ]
    summary=summarize_pts_frames(pts,frames)
    assert summary["mapped_source_indexes_strictly_increasing"] is False
    assert summary["duplicate_source_frame_matches"] == 1
    assert summary["pts_alignment_valid"] is False


def test_python_report_uses_actual_summary_disagreement_key():
    from scripts.phase10b_browser_lifecycle_analysis import report_text
    summary={"status":"completed","mode_count":4,"clip_count":24,"verified_clip_count":24,
             "condition_validation_disagreement_count":0,"weighted_source_coverage_pct":99.0,
             "source_frame_opportunities":402,"pts_aligned_clip_count":24,
             "pts_alignment_violation_count":0,"initial_frame_contract_valid_count":24}
    modes=[{"mode":"foreground_focused","clip_count":6,"verified_clip_count":6,"source_frame_coverage_pct":99.0,
            "hidden_complete_interval_count":0,"hidden_right_censored_interval_count":0,
            "hidden_right_censored_lower_bound_max_ms":None,"mode_exposure_confirmed":True}]
    text=report_text(modes,[],summary,[])
    assert "condition validation disagreements: **0**" in text
    assert "Strict half-frame PTS alignment: **24/24 clips**" in text


def test_verifier_checks_24_containers_and_all_four_modes_when_present():
    verifier = SCRIPTS / "verify_phase10b_bundle.py"
    if not verifier.exists():
        return  # verifier belongs to the original Phase 10B overlay, omitted from this diagnostic subset
    source=verifier.read_text(encoding="utf-8")
    assert 'len(indexes) != 4 or len(containers) != 24' in source
    assert 'classifier_checkpoint_loaded' in source
    assert 'official_split_count' in source
    assert 'phase10b_run_id' in source


def test_phase10b_protocol_metadata_is_compatible_without_reintroducing_callback_gate():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    server=(SCRIPTS/"serve_phase10b_capture.py").read_text(encoding="utf-8")
    # The shared ISLBRW01 protocol requires timer_interval_ms=40 as metadata.
    # The callback acceptance gate remains disabled; there is no interval timer.
    assert "timer_interval_ms: 40" in html
    assert '"timer_interval_ms": 40' in server
    assert "phase10b_callback_gate_ms: 0" in html
    assert '"phase10b_callback_gate_ms": 0' in server
    # Legacy timer_ticks must be >= submitted frame count in ISLBRW01.
    # It is populated as a compatibility count, not as real timer callbacks.
    assert "frame_count: frameRows.length, timer_ticks: frameRows.length" in html
    assert "lastAcceptedAt" not in html


def test_phase10b_metadata_passes_shared_islbrw01_protocol():
    import importlib, sys
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    protocol = importlib.import_module("browser_capture_protocol")
    frame_count = 12
    fake_jpegs = [b"\xff\xd8phase10b-test\xff\xd9" for _ in range(frame_count)]
    header = {
        "schema_version": 1,
        "capture_config": dict(protocol.EXPECTED_CONFIG),
        "case": {
            "video_path": "Animals/1. Dog/MVI_3060.MOV",
            "label": "dog",
            "source_name": "MVI_3060.MOV",
            "source_width": 1280,
            "source_height": 720,
            "capture_duration_ms": 500,
            "frame_count": frame_count,
            "timer_ticks": frame_count,
            "duplicate_frame_skips": 0,
            "browser_total_video_frames": frame_count,
            "browser_dropped_video_frames": 0,
            "frames": [
                {
                    "video_time_seconds": 0.04 * index,
                    "elapsed_ms": 40.0 * index,
                    "width": 640,
                    "height": 360,
                    "jpeg_bytes": len(fake_jpegs[index]),
                }
                for index in range(frame_count)
            ],
        },
    }
    payload = protocol.encode_capture(header, fake_jpegs)
    parsed = protocol.parse_capture_payload(payload)
    assert parsed.header["capture_config"]["timer_interval_ms"] == 40
    assert parsed.header["case"]["timer_ticks"] == frame_count
    assert len(parsed.frames) == frame_count


def test_visible_unfocused_blur_before_video_playing_rebases_exposure_start():
    """A pre-play blur must count only from the later video_playing event."""
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [
        _e("window_blur", 800, visibility_state="visible", has_focus=False,
           video_paused=True, video_current_time_seconds=0.0),
        _e("video_playing", 1000, visibility_state="visible", has_focus=False,
           video_paused=False, video_current_time_seconds=0.0),
        _e("window_focus", 2700, visibility_state="visible", has_focus=True,
           video_paused=False, video_current_time_seconds=1.7),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 3000)
    assert len(unfocused) == 1
    interval = unfocused[0]
    assert interval["opened_by"] == "window_blur_then_video_playing"
    assert interval["preplay_blur_start_epoch_ms"] == 800
    assert interval["preplay_blur_duration_ms"] == 200
    assert interval["start_epoch_ms"] == 1000
    assert interval["duration_ms"] == 1700
    assert interval["started_during_playback"] is True
    initial = {"visibility_state": "visible", "has_focus": True}
    ok, _, evidence = classify_condition("visible_unfocused", initial, [], timeline, None, hidden, unfocused)
    assert ok
    assert evidence["qualifying_interval"]["start_epoch_ms"] == 1000


def test_preplay_blur_does_not_count_toward_visible_unfocused_threshold():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [
        _e("window_blur", 700, visibility_state="visible", has_focus=False,
           video_paused=True, video_current_time_seconds=0.0),
        _e("video_playing", 1000, visibility_state="visible", has_focus=False,
           video_paused=False, video_current_time_seconds=0.0),
        _e("window_focus", 2450, visibility_state="visible", has_focus=True,
           video_paused=False, video_current_time_seconds=1.45),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 2800)
    assert unfocused[0]["duration_ms"] == 1450
    assert unfocused[0]["preplay_blur_duration_ms"] == 300
    initial = {"visibility_state": "visible", "has_focus": True}
    assert not classify_condition("visible_unfocused", initial, [], timeline, None, hidden, unfocused)[0]


def test_browser_lifecycle_js_rebases_preplay_blur_at_video_playing():
    html = (SCRIPTS / "browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert "window_blur_then_video_playing" in html
    assert "preplay_blur_start_epoch_ms" in html
    assert "blurOpen.started_during_playback !== true" in html


def _build_valid_phase10b_payload(row, mode="foreground_focused"):
    import sys
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from browser_capture_protocol import EXPECTED_CONFIG, encode_capture
    frame_bytes = [b"\xff\xd8phase10btest\xff\xd9" for _ in range(12)]
    frame_rows = []
    for index in range(12):
        frame_rows.append({
            "video_time_seconds": 0.04 * index,
            "elapsed_ms": 40.0 * index,
            "width": 640,
            "height": 360,
            "capture_kind": "initial_paused" if index == 0 else "rvfc_playback",
        })
    header = {
        "schema_version": 1,
        "capture_config": {**EXPECTED_CONFIG, "phase": "10B", "phase10b_mode": mode,
                            "phase10b_run_id": mode, "phase10b_callback_gate_ms": 0},
        "case": {
            "video_path": row["video_path"], "label": row["label"], "source_name": "clip.mov",
            "source_width": 1280, "source_height": 720, "capture_duration_ms": 500,
            "frame_count": 12, "timer_ticks": 12, "duplicate_frame_skips": 0,
            "browser_total_video_frames": 12, "browser_dropped_video_frames": 0,
            "phase10b_mode": mode, "phase10b_run_id": mode,
            "phase10b_capture_encoder": "sync",
            "phase10b_initial_frame_policy": "paused_draw_encode_before_play",
            "phase10b_condition_validation": {"valid": True},
            "frames": frame_rows,
        },
    }
    return encode_capture(header, frame_bytes)


def test_resume_completed_foreground_pass_validates_all_six_protocol_containers(tmp_path):
    import hashlib
    import json
    from scripts.phase10b_browser_lifecycle_orchestrator import TARGET_PATHS, target_key, validate_existing_capture_dir
    rows = [{"video_path": path, "label": f"label_{i}"} for i, path in enumerate(TARGET_PATHS)]
    out = tmp_path / "phase10b_capture_foreground_focused_20261108_110000"
    (out / "clips").mkdir(parents=True)
    received = []
    for index, row in enumerate(rows):
        payload = _build_valid_phase10b_payload(row)
        name = f"clips/clip_{index}.islbrw"
        (out / name).write_bytes(payload)
        received.append({"video_path": row["video_path"], "container": name,
                         "sha256": hashlib.sha256(payload).hexdigest()})
    index = {
        "official_split_count": 77, "received_count": 6,
        "capture_config": {"phase10b_mode": "foreground_focused", "phase10b_run_id": "foreground_focused"},
        "received": received,
    }
    (out / "capture_index.json").write_text(json.dumps(index), encoding="utf-8")
    result = validate_existing_capture_dir(out, mode="foreground_focused", rows=rows,
                                           target_paths={target_key(x) for x in TARGET_PATHS})
    assert result["reused"] is True
    assert result["received"] == 6
    assert result["output_dir"] == out.resolve()


def test_resume_completed_foreground_pass_rejects_invalid_condition_evidence(tmp_path):
    import hashlib
    import json
    import sys
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from browser_capture_protocol import parse_capture_payload
    from scripts.phase10b_browser_lifecycle_orchestrator import TARGET_PATHS, target_key, validate_existing_capture_dir
    rows = [{"video_path": path, "label": f"label_{i}"} for i, path in enumerate(TARGET_PATHS)]
    out = tmp_path / "phase10b_capture_foreground_focused_20261108_110001"
    (out / "clips").mkdir(parents=True)
    received = []
    for index, row in enumerate(rows):
        payload = _build_valid_phase10b_payload(row)
        if index == 0:
            header_len = int.from_bytes(payload[8:12], "big")
            header = json.loads(payload[12:12+header_len])
            header["case"]["phase10b_condition_validation"]["valid"] = False
            from browser_capture_protocol import encode_capture
            payload = encode_capture(header, [b"\xff\xd8phase10btest\xff\xd9" for _ in range(12)])
        name = f"clips/clip_{index}.islbrw"
        (out / name).write_bytes(payload)
        received.append({"video_path": row["video_path"], "container": name,
                         "sha256": hashlib.sha256(payload).hexdigest()})
    (out / "capture_index.json").write_text(json.dumps({
        "official_split_count":77,"received_count":6,
        "capture_config":{"phase10b_mode":"foreground_focused","phase10b_run_id":"foreground_focused"},
        "received":received}),encoding="utf-8")
    import pytest
    with pytest.raises(RuntimeError, match="successful client-side condition evidence"):
        validate_existing_capture_dir(out, mode="foreground_focused", rows=rows,
                                      target_paths={target_key(x) for x in TARGET_PATHS})


def _hb(t, *, visible='visible', focus=False, paused=False, ended=False):
    return {
        'wall_time_epoch_ms': t,
        'elapsed_ms': t,
        'visibility_state': visible,
        'has_focus': focus,
        'video_paused': paused,
        'video_ended': ended,
        'current_time_seconds': max(0.0, (t - 1000) / 1000),
    }


def test_visible_unfocused_condition_can_be_proved_by_heartbeat_samples_without_blur_event():
    from scripts.phase10b_browser_lifecycle_analysis import classify_condition, lifecycle_intervals
    heartbeats = [_hb(1000 + i * 100) for i in range(18)]
    timeline = []  # Some browsers/OS focus transitions do not produce a usable window.blur event.
    hidden, unfocused = lifecycle_intervals(timeline, 2800)
    ok, reason, evidence = classify_condition(
        'visible_unfocused',
        {'visibility_state': 'visible', 'has_focus': False}, [], timeline, None,
        hidden, unfocused, heartbeats, 2800,
        {'visibility_state': 'visible', 'has_focus': False, 'video_ended': True},
    )
    assert ok
    assert 'heartbeat_state_sampling' in reason
    assert evidence['heartbeat_intervals'][0]['heartbeat_sample_count'] == 18
    assert evidence['qualifying_interval']['duration_ms'] >= 1500


def test_heartbeat_visible_unfocused_requires_contiguous_observations():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals
    heartbeats = [_hb(1000), _hb(1100), _hb(1200), _hb(1900), _hb(2000), _hb(2100)]
    intervals = heartbeat_visible_unfocused_intervals(
        heartbeats, 2200,
        {'visibility_state': 'visible', 'has_focus': False},
        max_gap_ms=450,
    )
    assert len(intervals) == 2
    assert intervals[0]['duration_ms'] == 200
    assert intervals[1]['duration_ms'] == 200
    assert all(x['duration_ms'] < 1500 for x in intervals)


def test_heartbeat_unfocused_open_interval_is_right_censored_at_playback_end():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals
    heartbeats = [_hb(1000 + i * 100) for i in range(16)]
    intervals = heartbeat_visible_unfocused_intervals(
        heartbeats, 2600,
        {'visibility_state': 'visible', 'has_focus': False},
        max_gap_ms=450,
    )
    assert len(intervals) == 1
    assert intervals[0]['censored'] is True
    assert intervals[0]['end_reason'] == 'capture_end_right_censored_heartbeat_lower_bound'
    assert intervals[0]['duration_ms'] == 1500


def test_heartbeat_unfocused_does_not_extend_when_page_returns_to_focus():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals
    heartbeats = [_hb(1000 + i * 100) for i in range(10)]
    intervals = heartbeat_visible_unfocused_intervals(
        heartbeats, 2500,
        {'visibility_state': 'visible', 'has_focus': True},
        max_gap_ms=450,
    )
    assert intervals[0]['duration_ms'] == 900
    assert intervals[0]['censored'] is False


def test_phase10b_rejected_attempts_are_persisted_without_capture_upload():
    source=(SCRIPTS/'serve_phase10b_capture.py').read_text(encoding='utf-8')
    html=(SCRIPTS/'browser_validation_capture_phase10b.html').read_text(encoding='utf-8')
    assert '"/api/attempt-diagnostic"' in source
    assert 'phase10b_attempt_diagnostics.jsonl' in source
    assert "fetch('/api/attempt-diagnostic'" in html
    assert "phase10b_attempt_diagnostics.jsonl" in source


def test_phase10b_visible_unfocused_uses_100ms_heartbeat_evidence_and_live_progress():
    html=(SCRIPTS/'browser_validation_capture_phase10b.html').read_text(encoding='utf-8')
    assert 'heartbeatVisibleUnfocusedIntervals' in html
    assert 'Visible/unfocused evidence:' in html
    assert 'heartbeat_state_sampling' in html
    assert 'HEARTBEAT_INTERVAL_MS = 100' in html


def test_heartbeat_visible_unfocused_counts_consecutive_actual_samples_and_not_drain_time():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals, lifecycle_intervals, classify_condition
    heartbeats = [
        {"wall_time_epoch_ms": 1000 + 100*i, "visibility_state": "visible", "has_focus": False,
         "video_paused": False, "video_ended": False}
        for i in range(16)
    ]
    # The analyzer's end epoch is the video-ended event; final pageState is sampled
    # during drain and therefore says paused/ended. Duration must remain last-first.
    final = {"visibility_state": "visible", "has_focus": False, "video_paused": True, "video_ended": True}
    intervals = heartbeat_visible_unfocused_intervals(heartbeats, 2700, final)
    assert len(intervals) == 1
    assert intervals[0]["duration_ms"] == 1500
    assert intervals[0]["censored"] is True
    hidden, unfocused = lifecycle_intervals([], 2700)
    ok, _, evidence = classify_condition("visible_unfocused", {"visibility_state":"visible","has_focus":True},
        [{"capture_kind":"initial_paused"}], [], None, hidden, unfocused, heartbeats, 2700, final)
    assert ok
    assert evidence["qualifying_interval"]["opened_by"] == "heartbeat_state_sampling"


def test_heartbeat_inactive_sample_breaks_interval_and_cannot_be_bridged():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals
    heartbeats=[]
    for t in range(1000, 1900, 100):
        heartbeats.append({"wall_time_epoch_ms":t,"visibility_state":"visible","has_focus":False,"video_paused":False,"video_ended":False})
    heartbeats.append({"wall_time_epoch_ms":1900,"visibility_state":"visible","has_focus":True,"video_paused":False,"video_ended":False})
    for t in range(2000, 2900, 100):
        heartbeats.append({"wall_time_epoch_ms":t,"visibility_state":"visible","has_focus":False,"video_paused":False,"video_ended":False})
    intervals=heartbeat_visible_unfocused_intervals(heartbeats,3000,{"visibility_state":"visible","has_focus":False,"video_paused":False,"video_ended":False})
    assert len(intervals)==2
    assert [x["duration_ms"] for x in intervals]==[800,800]
    assert max(x["duration_ms"] for x in intervals)<1500


def test_heartbeat_gaps_over_450ms_break_exposure_interval():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals
    heartbeats=[]
    for t in (1000,1100,1200,1900,2000,2100):
        heartbeats.append({"wall_time_epoch_ms":t,"visibility_state":"visible","has_focus":False,"video_paused":False,"video_ended":False})
    intervals=heartbeat_visible_unfocused_intervals(heartbeats,2200,{"visibility_state":"visible","has_focus":False})
    assert len(intervals)==2
    assert all(x["duration_ms"]==200 for x in intervals)


def test_visible_unfocused_playback_is_armed_and_triggered_by_window_blur():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert "visible_unfocused_armed" in html
    assert "playback_start_triggered" in html
    assert "startPlaybackFn('window_blur_visible_unfocused')" in html
    assert "already_visible_unfocused_when_armed" in html
    assert "video.muted = true" in html
    assert "arm_timeout_ms: 10000" in html


def test_focus_regain_event_splits_heartbeat_run_even_without_timer_sample():
    from scripts.phase10b_browser_lifecycle_analysis import heartbeat_visible_unfocused_intervals
    heartbeats=[]
    for t in range(1000,1900,100):
        heartbeats.append({"wall_time_epoch_ms":t,"visibility_state":"visible","has_focus":False,"video_paused":False,"video_ended":False})
    for t in range(2000,2900,100):
        heartbeats.append({"wall_time_epoch_ms":t,"visibility_state":"visible","has_focus":False,"video_paused":False,"video_ended":False})
    events=[
        _e("window_focus",1850,visibility_state="visible",has_focus=True,video_paused=False,video_ended=False),
        _e("window_blur",1950,visibility_state="visible",has_focus=False,video_paused=False,video_ended=False),
    ]
    intervals=heartbeat_visible_unfocused_intervals(heartbeats,3000,
        {"visibility_state":"visible","has_focus":False},state_events=events)
    assert len(intervals)==2
    assert [x["duration_ms"] for x in intervals]==[800,850]


def test_phase10b_rejected_attempt_telemetry_is_persisted_without_capture_upload():
    server=(SCRIPTS/"serve_phase10b_capture.py").read_text(encoding="utf-8")
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert 'request_path == "/api/attempt-diagnostic"' in server
    assert 'phase10b_attempt_diagnostics.jsonl' in server
    assert "await saveRejectedAttemptDiagnostic(attemptDiagnostic)" in html
    assert "return { accepted: false, reason: conditionValidation.reason" in html
    assert "fetch('/api/capture'" in html
    orchestrator=(SCRIPTS/"phase10b_browser_lifecycle_orchestrator.py").read_text(encoding="utf-8")
    assert 'phase10b_attempt_diagnostics.jsonl' in orchestrator
    assert 'rejected_attempt_diagnostics=' in orchestrator


def test_phase10b_html_declares_capture_finish_epoch_before_header_serialization():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    finish_decl = html.index("const captureFinishEpoch = Number(")
    header_start = html.index("const caseHeader = {")
    serialized_use = html.index("capture_finish_wall_time_epoch_ms: captureFinishEpoch")
    assert finish_decl < header_start < serialized_use
    assert html.count("const captureFinishEpoch = Number(") == 1


def test_visible_unfocused_has_focus_state_polling_fallback_and_clear_instructions():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert "heartbeat_detected_visible_unfocused" in html
    assert "sample.visibility_state === 'visible' && sample.has_focus === false" in html
    assert "After the page displays ARMED, click the second application window" in html


def test_hidden_background_accepts_interval_closed_after_video_ends():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [
        _e("document_visibilitychange", 1000, visibility_state_after="hidden", video_paused=False,
           video_ended=False, video_current_time_seconds=1.1),
        _e("video_ended_drain_started", 2040, visibility_state="hidden", video_paused=True,
           video_ended=True, video_current_time_seconds=2.04),
        _e("document_visibilitychange", 3100, visibility_state_after="visible", video_paused=True,
           video_ended=True, video_current_time_seconds=2.04),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 3300)
    assert len(hidden) == 1
    assert hidden[0]["started_during_playback"] is True
    assert hidden[0]["duration_ms"] == 2100
    assert hidden[0]["censored"] is False
    ok, note, evidence = classify_condition(
        "hidden_background", {"visibility_state":"visible", "has_focus":True}, [],
        timeline, None, hidden, unfocused
    )
    assert ok is True
    assert "closed by visible return" in note
    assert evidence["qualifying_interval"]["duration_ms"] == 2100


def test_hidden_background_rejects_right_censored_interval_even_above_threshold():
    from scripts.phase10b_browser_lifecycle_analysis import lifecycle_intervals, classify_condition
    timeline = [
        _e("document_visibilitychange", 1000, visibility_state_after="hidden", video_paused=False,
           video_ended=False, video_current_time_seconds=0.8),
    ]
    hidden, unfocused = lifecycle_intervals(timeline, 4000)
    assert hidden[0]["duration_ms"] == 3000
    assert hidden[0]["censored"] is True
    ok, note, evidence = classify_condition(
        "hidden_background", {"visibility_state":"visible", "has_focus":True}, [],
        timeline, None, hidden, unfocused
    )
    assert ok is False
    assert evidence["qualifying_interval"] is None
    assert "closed hidden exposure" in note


def test_hidden_background_capture_waits_for_visible_return_after_video_end():
    html=(SCRIPTS/"browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
    assert "hiddenReturnPending" in html
    assert "hidden_return_wait_started" in html
    assert "hidden_return_wait_completed" in html
    assert "finishCaptureNow()" in html
    assert "!item.censored" in html
    assert "phase10b_condition_enforcement_version: 2" in html


def test_orchestrator_can_reuse_completed_visible_unfocused_pass():
    orchestrator=(SCRIPTS/"phase10b_browser_lifecycle_orchestrator.py").read_text(encoding="utf-8")
    launcher=(SCRIPTS/"run_phase10b_browser_lifecycle.ps1").read_text(encoding="utf-8")
    assert "--resume-visible-unfocused-dir" in orchestrator
    assert "resumed_visible_unfocused_candidate" in orchestrator
    assert 'mode="visible_unfocused"' in orchestrator
    assert "if mode == \"visible_unfocused\" and resumed_visible_unfocused is not None" in orchestrator
    assert "$ResumeVisibleUnfocusedDir" in launcher
    assert "--resume-visible-unfocused-dir" in launcher
