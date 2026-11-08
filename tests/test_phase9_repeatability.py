from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT / "src", SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import phase9_repeatability_analysis as analysis
from browser_capture_protocol import encode_capture, parse_capture_payload


def test_nearest_index_maps_zero_to_first_source_frame():
    idx, delta = analysis.nearest_index([0.0, 0.04, 0.08], 0.0)
    assert idx == 0
    assert delta == 0.0


def test_nearest_index_selects_nearest_pts_not_nominal_fps():
    pts = [0.0, 0.041, 0.083, 0.126]
    idx, delta = analysis.nearest_index(pts, 0.084)
    assert idx == 2
    assert abs(delta - 0.001) < 1e-9


def test_missing_ranges_identifies_leading_interior_and_terminal_gaps():
    assert analysis.missing_ranges({1, 2, 5}, 7) == [[0, 0], [3, 4], [6, 6]]


def test_jaccard_repeatability():
    assert analysis.jaccard({0, 1, 2}, {0, 1, 2}) == 1.0
    assert analysis.jaccard({0, 1, 2}, {1, 2, 3}) == 0.5


def test_html_encodes_paused_frame_before_playback_and_has_no_gate():
    html = (SCRIPTS / "browser_validation_capture_phase9.html").read_text(encoding="utf-8")
    initial = html.index("const initialDrawStart")
    play = html.index("video.play().then")
    assert initial < play
    assert "capture_kind: 'initial_paused'" in html
    assert "phase9_initial_frame_policy: 'paused_draw_encode_before_play'" in html
    assert "gateMs" not in html
    assert "requestVideoFrameCallback" in html


def test_html_has_sync_and_async_encoders_and_three_repeats():
    html = (SCRIPTS / "browser_validation_capture_phase9.html").read_text(encoding="utf-8")
    assert "canvas.toDataURL('image/jpeg', CONFIG.jpeg_quality)" in html
    assert "canvas.toBlob" in html
    assert "[1, 2, 3].includes(REPEAT)" in html
    assert "pending_encodes_after_flush" in html


def test_html_template_tokens_are_server_substitutable():
    html = (SCRIPTS / "browser_validation_capture_phase9.html").read_text(encoding="utf-8")
    assert "__PHASE9_ENCODER__" in html
    assert "__PHASE9_REPEAT__" in html
    assert "__PHASE9_RUN_ID__" in html


def test_phase9_config_preserves_protocol_dimensions_and_iot_lock():
    html = (SCRIPTS / "browser_validation_capture_phase9.html").read_text(encoding="utf-8")
    assert "canvas_width: 640" in html
    assert "jpeg_quality: 0.92" in html
    assert "max_capture_seconds: 6.0" in html
    assert "max_frames: 169" in html
    assert "iot_enabled: false" in html
    assert "iot_command_emission: false" in html


def test_capture_protocol_accepts_initial_frame_as_first_monotonic_record():
    frame = b"\xff\xd8testjpeg\xff\xd9"
    rows = []
    for index in range(12):
        rows.append({
            "video_time_seconds": index * 0.04,
            "elapsed_ms": index * 40,
            "width": 640,
            "height": 360,
            "jpeg_bytes": len(frame),
        })
    header = {
        "schema_version": 1,
        "capture_config": {
            "canvas_width": 640, "jpeg_quality": 0.92, "timer_interval_ms": 40,
            "max_capture_seconds": 6.0, "max_frames": 169, "diagnostic_mode": True,
            "iot_enabled": False, "iot_command_emission": False, "classifier_input_mirrored": False,
            "phase": 9, "phase9_encoder": "sync", "phase9_repeat": 1, "phase9_run_id": "sync_r1",
            "phase9_callback_gate_ms": 0, "phase9_initial_frame_policy": "paused_draw_encode_before_play",
            "phase9_initial_frame_timestamp_seconds": 0,
        },
        "case": {
            "video_path": "test/clip.mov", "label": "hello", "source_name": "clip.mov",
            "source_width": 640, "source_height": 360, "frame_count": 12, "frames": rows,
            "timer_ticks": 12, "duplicate_frame_skips": 0, "browser_total_video_frames": 12,
            "browser_dropped_video_frames": 0, "capture_duration_ms": 500,
        },
    }
    payload = encode_capture(header, [frame] * 12)
    parsed = parse_capture_payload(payload, {"video_path": "test/clip.mov", "label": "hello"})
    assert len(parsed.frames) == 12
    assert parsed.header["capture_config"]["phase9_initial_frame_timestamp_seconds"] == 0


def test_analyzer_tracks_three_repeat_group_options():
    script = (SCRIPTS / "phase9_repeatability_analysis.py").read_text(encoding="utf-8")
    assert 'parser.add_argument(f"--{encoder}-r{repeat}-dir"' in script
    assert 'dest=f"{encoder}_r{repeat}_dir"' in script


def test_runner_is_a_small_argument_forwarding_wrapper():
    script = (SCRIPTS / "run_phase9_repeatability.ps1").read_text(encoding="utf-8")
    assert "phase9_orchestrator.py" in script
    assert "--repo" in script and "--data-root" in script
    assert "--manifest" in script and "--ffprobe" in script
    assert "$RunnerArgs" in script
    assert "Invoke-Phase9CapturePass" not in script


def test_python_orchestrator_orders_six_passes_and_keeps_diagnostic_lockdown():
    import phase9_orchestrator as orchestrator
    assert orchestrator.PASS_SPECS == (
        ("sync", 1, 8791), ("async", 1, 8792), ("async", 2, 8793),
        ("sync", 2, 8794), ("sync", 3, 8795), ("async", 3, 8796),
    )
    assert "ISL_IOT_ENABLED" in (SCRIPTS / "phase9_orchestrator.py").read_text(encoding="utf-8")
    assert "pre_iot_lockdown" in (SCRIPTS / "phase9_orchestrator.py").read_text(encoding="utf-8")


def test_analyzer_verifies_initial_paused_frame_and_full_capture_set():
    pts = [0.0, 0.04, 0.08]
    meta = {"pts_step_median_ms": 40.0, "source_video_sha256": "abc"}
    case = {
        "phase9_run_id": "sync_r1", "video_path": "test/clip.mov", "label": "hello",
        "frames": [
            {"capture_kind": "initial_paused", "video_time_seconds": 0.0, "elapsed_ms": 0.0,
             "presented_frames": None, "visibility_state": "visible", "has_focus": True,
             "video_paused": True, "capture_current_time_seconds": 0.0,
             "draw_duration_ms": 1.0, "encode_latency_ms": 2.0},
            {"capture_kind": "rvfc_playback", "video_time_seconds": 0.04, "elapsed_ms": 40.0,
             "presented_frames": 2, "callback_time_ms": 40.0, "visibility_state": "visible",
             "has_focus": True, "draw_duration_ms": 1.0, "callback_work_ms": 2.0, "encode_latency_ms": 2.0},
            {"capture_kind": "rvfc_playback", "video_time_seconds": 0.08, "elapsed_ms": 80.0,
             "presented_frames": 3, "callback_time_ms": 80.0, "visibility_state": "visible",
             "has_focus": True, "draw_duration_ms": 1.0, "callback_work_ms": 2.0, "encode_latency_ms": 2.0},
        ],
        "callback_requested_count": 2, "callback_delivered_count": 2,
        "callbacks_scheduled_not_delivered": 0, "duplicate_media_callback_skips": 0,
        "max_pending_encodes": 0, "pending_encodes_after_flush": 0,
        "video_ended_at_finish": True, "video_current_time_at_end": 0.12,
        "browser_dropped_video_frames": 0, "phase9_end_drain_grace_ms": 250,
        "timeline_events": [{"event_type": "initial_paused_frame_captured"}, {"event_type": "play_invoked"}], "heartbeat_samples": [],
    }
    summary, rows, _, _ = analysis.analyze_capture("sync", 1, case, pts, meta, (b"a", b"b", b"c"))
    assert summary["initial_paused_frame_count"] == 1
    assert summary["first_record_is_initial_paused"] is True
    assert summary["initial_frame_maps_to_source_index_zero"] is True
    assert summary["initial_frame_timestamp_zero"] is True
    assert summary["initial_frame_video_paused_at_zero"] is True
    assert summary["initial_frame_event_precedes_play"] is True
    assert summary["source_frame_coverage_pct"] == 100.0
    assert len(rows[0]["jpeg_sha256"]) == 64
    assert summary["duplicate_source_frame_matches"] == 0
    assert len(rows) == 3


def test_initial_frame_timestamp_mismatch_is_visible_in_analysis():
    pts = [0.0, 0.04, 0.08]
    meta = {"pts_step_median_ms": 40.0, "source_video_sha256": "abc"}
    case = {
        "phase9_run_id": "async_r1", "video_path": "test/clip.mov", "label": "hello",
        "frames": [
            {"capture_kind": "initial_paused", "video_time_seconds": 0.04, "elapsed_ms": 0.0,
             "presented_frames": None, "visibility_state": "visible", "has_focus": True,
             "video_paused": True, "capture_current_time_seconds": 0.04},
            {"capture_kind": "rvfc_playback", "video_time_seconds": 0.08, "elapsed_ms": 40.0,
             "presented_frames": 2, "callback_time_ms": 40.0, "visibility_state": "visible", "has_focus": True},
        ],
        "callback_requested_count": 1, "callback_delivered_count": 1,
        "timeline_events": [{"event_type": "initial_paused_frame_captured"}, {"event_type": "play_invoked"}],
    }
    summary, _, _, _ = analysis.analyze_capture("async", 1, case, pts, meta)
    assert summary["first_record_is_initial_paused"] is True
    assert summary["initial_frame_maps_to_source_index_zero"] is False
    assert summary["leading_source_frames_missing"] == 1


def test_bundle_verifier_uses_sha256_manifest():
    script = (SCRIPTS / "verify_phase9_bundle.py").read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" in script
    assert "archive.testzip()" in script


def test_orchestrator_targets_are_exactly_the_six_diagnostic_clips():
    import phase9_orchestrator as orchestrator
    assert len(orchestrator.TARGET_PATHS) == 6
    assert len({p.casefold() for p in orchestrator.TARGET_PATHS}) == 6


def test_orchestrator_bundle_hash_uses_sha256(tmp_path):
    import hashlib
    import phase9_orchestrator as orchestrator
    source = tmp_path / "sample.bin"
    source.write_bytes(b"phase9")
    assert orchestrator.sha256_file(source) == hashlib.sha256(b"phase9").hexdigest()


def test_orchestrator_safety_truth_values():
    import phase9_orchestrator as orchestrator
    assert orchestrator._truthy("true")
    assert orchestrator._truthy("YES")
    assert orchestrator._truthy("1")
    assert not orchestrator._truthy("false")
    assert not orchestrator._truthy(None)


def test_orchestrator_creates_verified_bundle_without_self_referential_hash(tmp_path):
    import hashlib
    import zipfile
    import phase9_orchestrator as orchestrator

    stage = tmp_path / "stage"
    (stage / "results").mkdir(parents=True)
    (stage / "results" / "report.txt").write_text("phase9 evidence", encoding="utf-8")
    bundle = tmp_path / "phase9.zip"
    digest = orchestrator.create_bundle(stage, bundle, Path(sys.executable), ROOT)

    assert digest == hashlib.sha256(bundle.read_bytes()).hexdigest()
    with zipfile.ZipFile(bundle) as archive:
        sums = archive.read("SHA256SUMS.txt").decode("utf-8").splitlines()
        assert all(not line.endswith("SHA256SUMS.txt") for line in sums)
        listed = {line.split("  ", 1)[1] for line in sums}
        assert listed == {"results/report.txt"}


def test_orchestrator_python_entrypoint_has_expected_cli_arguments():
    script = (SCRIPTS / "phase9_orchestrator.py").read_text(encoding="utf-8")
    for argument in ("--repo", "--data-root", "--manifest", "--ffprobe"):
        assert f'parser.add_argument("{argument}"' in script
    assert "subprocess.Popen" in script
    html = (SCRIPTS / "browser_validation_capture_phase9.html").read_text(encoding="utf-8")
    assert "requestVideoFrameCallback" in html
