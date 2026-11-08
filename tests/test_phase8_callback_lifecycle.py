from __future__ import annotations
import importlib.util
import pathlib
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "phase8_callback_lifecycle_analysis.py"
spec = importlib.util.spec_from_file_location("phase8_analysis", SCRIPT)
phase8 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = phase8
assert spec.loader is not None
spec.loader.exec_module(phase8)


def test_missing_ranges_empty_and_complete():
    assert phase8.missing_ranges([], 4) == [[0, 3]]
    assert phase8.missing_ranges([0, 1, 2, 3], 4) == []


def test_missing_ranges_multiple_spans():
    assert phase8.missing_ranges([0, 2, 3, 7], 9) == [[1, 1], [4, 6], [8, 8]]


def test_factor_modes_cover_gate_and_encoder_factors():
    assert phase8.MODE_FACTORS == {
        "gate_sync": ("gated", "sync"),
        "ungate_sync": ("ungated", "sync"),
        "gate_async": ("gated", "async"),
        "ungate_async": ("ungated", "async"),
    }


def test_ffprobe_nearest_index_chooses_nearest_neighbor():
    assert phase8.nearest_index(0.079, [0.0, 0.04, 0.08, 0.12]) == (2, -0.0010000000000000009)


def test_reported_browser_quality_counter_not_used_as_coverage_denominator():
    # Coverage is source PTS index set size / FFprobe source frame count; the
    # independent browser quality counter is not part of this calculation.
    pts = [i * 0.04 for i in range(10)]
    indexes = [phase8.nearest_index(t, pts)[0] for t in [0.0, 0.04, 0.08, 0.12]]
    assert len(set(indexes)) / len(pts) == 0.4


def test_browser_template_contains_all_four_modes_and_telemetry():
    html = (SCRIPT.parent / "browser_validation_capture_phase8.html").read_text(encoding="utf-8")
    for mode in ("gate_sync", "ungate_sync", "gate_async", "ungate_async"):
        assert mode in html
    for token in ("document.visibilityState", "document.hasFocus()", "'waiting'", "video_${name}", "heartbeat_samples", "rvfc_silence_detected", "callback_requested_count"):
        assert token in html


def test_phase8_server_does_not_bind_non_loopback():
    server = (SCRIPT.parent / "serve_phase8_capture.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", args.port)' in server
    assert '"__PHASE8_MODE__", json.dumps(self.server.capture_mode)' in server


def test_mode_placeholder_is_unquoted_for_json_literal_substitution():
    html = (SCRIPT.parent / "browser_validation_capture_phase8.html").read_text(encoding="utf-8")
    assert "const MODE = __PHASE8_MODE__;" in html
    assert "const MODE = '__PHASE8_MODE__';" not in html


def test_capture_listeners_are_cleaned_on_all_exit_paths():
    html = (SCRIPT.parent / "browser_validation_capture_phase8.html").read_text(encoding="utf-8")
    assert "for (const [target, name, fn] of listeners) target.removeEventListener(name, fn);" in html
    assert "if (onVisibility) document.removeEventListener('visibilitychange', onVisibility);" in html
    assert "if (onFocus) window.removeEventListener('focus', onFocus);" in html
    assert "if (onBlur) window.removeEventListener('blur', onBlur);" in html
    assert "for (const [target,name,fn] of [])" not in html


def test_gate_rejection_does_not_pollute_delivered_callback_gap_metrics():
    html = (SCRIPT.parent / "browser_validation_capture_phase8.html").read_text(encoding="utf-8")
    delivered_update = html.index("stats.lastDeliveredPresentedFrames = presentedFrames;")
    gate_check = html.index("if (SPEC.gateMs > 0 && callbackNow - stats.lastAcceptedCallbackAt < SPEC.gateMs)")
    assert delivered_update < gate_check
    assert "stats.callbackGaps.push(callbackNow - stats.lastDeliveredCallbackAt)" in html
    assert "stats.presentedGapTotal += gap" in html


def test_coverage_is_unique_ffprobe_source_frames_not_browser_counters():
    case = {
        "video_path": "Demo/clip.mov",
        "label": "hello",
        "frames": [
            {"video_time_seconds": 0.001, "elapsed_ms": 0, "callback_time_ms": 100, "presented_frames": 1,
             "callback_work_ms": 1, "draw_duration_ms": 0.3, "encode_latency_ms": 2, "visibility_state": "visible", "has_focus": True},
            {"video_time_seconds": 0.081, "elapsed_ms": 80, "callback_time_ms": 180, "presented_frames": 3,
             "callback_work_ms": 1, "draw_duration_ms": 0.3, "encode_latency_ms": 2, "visibility_state": "visible", "has_focus": True},
        ],
        "timeline_events": [], "heartbeat_samples": [], "capture_duration_ms": 100,
        "callback_requested_count": 3, "callback_delivered_count": 3,
        "callbacks_scheduled_not_delivered": 0, "rate_limited_callback_skips": 1,
        "duplicate_media_callback_skips": 0, "max_pending_encodes": 1,
        "quality_before": {"total_video_frames": 0, "dropped_video_frames": 0},
        "quality_at_end": {"total_video_frames": 99, "dropped_video_frames": 4},
        "video_duration_seconds": 0.16, "video_current_time_at_end": 0.16,
        "video_ended_at_finish": True,
    }
    pts = [0.0, 0.04, 0.08, 0.12]
    meta = {"pts_step_median_ms": 40.0, "source_video_sha256": "test"}
    summary, *_ = phase8.summarize_capture("gate_sync", case, pts, meta)
    assert summary["unique_source_frames_captured"] == 2
    assert summary["source_frame_count"] == 4
    assert summary["source_frame_coverage_pct"] == 50.0
    assert summary["callbacks_rejected_by_gate"] == 1
    assert summary["quality_total_video_frames_delta"] == 99
    assert summary["presentedFrames_interior_gap_total"] == 1


def test_runner_invokes_all_four_separate_factorial_passes():
    runner = (SCRIPT.parent / "run_phase8_callback_lifecycle.ps1").read_text(encoding="utf-8")
    for mode in ("gate_sync", "ungate_sync", "gate_async", "ungate_async"):
        assert f"Invoke-Phase8CapturePass -Mode '{mode}'" in runner
    assert "No application, classifier, MQTT or ESP32 endpoints are exposed." in (SCRIPT.parent / "serve_phase8_capture.py").read_text(encoding="utf-8")


def test_ffprobe_preflight_uses_start_process_file_redirection():
    runner = (SCRIPT.parent / "run_phase8_callback_lifecycle.ps1").read_text(encoding="utf-8")
    assert "Start-Process -FilePath $FfprobePath" in runner
    assert "-RedirectStandardOutput $ProbeStdout" in runner
    assert "-RedirectStandardError $ProbeStderr" in runner
    assert "if ($probeExit -ne 0)" in runner
    assert "& $FfprobePath -version 2>&1 | Select-Object -First 1" not in runner
