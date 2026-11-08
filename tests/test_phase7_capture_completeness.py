from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import pytest
from phase7_capture_completeness import nearest_pts, percentile, sequence_gap_metrics, summarize_clip


def test_sequence_gap_metrics_counts_intermediate_and_total_unseen_frames():
    result = sequence_gap_metrics([2, 4, 7], total_presented=8)
    assert result == {
        "interior_presented_frame_gaps": 3,
        "initial_presented_frame_gap": 1,
        "trailing_presented_frame_gap": 1,
        "unseen_presented_frames": 5,
        "max_interior_gap": 2,
    }


def test_sequence_gap_metrics_no_gaps_when_consecutive_counter_values():
    result = sequence_gap_metrics([1, 2, 3, 4], total_presented=4)
    assert result["unseen_presented_frames"] == 0
    assert result["interior_presented_frame_gaps"] == 0


def test_sequence_gap_metrics_rejects_non_monotonic_values():
    with pytest.raises(ValueError, match="strictly increasing"):
        sequence_gap_metrics([2, 2, 3], total_presented=3)


def test_percentile_uses_nearest_rank_and_handles_empty_values():
    assert percentile([1, 2, 3, 4, 5], 0.95) == 5.0
    assert percentile([], 0.95) is None


def test_nearest_pts_returns_exact_frame_index_and_signed_delta():
    index, delta = nearest_pts(0.081, [0.0, 0.04, 0.08, 0.12])
    assert index == 2
    assert delta == pytest.approx(0.001)


def test_nearest_pts_tie_resolves_to_earlier_timestamp():
    index, delta = nearest_pts(0.06, [0.0, 0.04, 0.08, 0.12])
    assert index == 1
    assert delta == pytest.approx(0.02)


def test_summarize_clip_reports_frame_gaps_pts_and_encode_queue():
    case = {
        "video_path": "a/b/demo.MOV",
        "label": "demo",
        "timer_ticks": 5,
        "browser_total_video_frames": 5,
        "browser_dropped_video_frames": 0,
        "duplicate_frame_skips": 1,
        "rate_limited_callback_skips": 0,
        "duplicate_media_callback_skips": 1,
        "capture_duration_ms": 200.0,
        "max_pending_encodes": 2,
        "mean_pending_encodes_at_enqueue": 1.5,
        "callback_work_mean_ms": 1.0,
        "draw_duration_mean_ms": 0.3,
        "encode_latency_mean_ms": 6.0,
        "frames": [
            {"video_time_seconds": 0.0, "presented_frames": 1, "callback_time_ms": 1.0, "callback_work_ms": 0.8, "draw_duration_ms": 0.2, "encode_latency_ms": 5.0, "pending_encodes_at_enqueue": 1, "jpeg_bytes": 100},
            {"video_time_seconds": 0.04, "presented_frames": 2, "callback_time_ms": 41.0, "callback_work_ms": 1.0, "draw_duration_ms": 0.3, "encode_latency_ms": 6.0, "pending_encodes_at_enqueue": 2, "jpeg_bytes": 110},
            {"video_time_seconds": 0.12, "presented_frames": 4, "callback_time_ms": 121.0, "callback_work_ms": 1.2, "draw_duration_ms": 0.4, "encode_latency_ms": 7.0, "pending_encodes_at_enqueue": 2, "jpeg_bytes": 120},
        ],
    }
    summary, frames = summarize_clip(
        "every_presented_frame",
        case,
        case["frames"],
        [0.0, 0.04, 0.08, 0.12, 0.16],
        {"source_pts_step_median_seconds": 0.04},
    )
    assert summary["capture_frame_count"] == 3
    assert summary["unseen_presented_frames"] == 2
    assert summary["interior_presented_frame_gaps"] == 1
    assert summary["max_pending_encodes"] == 2
    assert summary["pts_matches_within_half_frame"] == 3
    assert frames[2]["source_pts_frame_index"] == 3
    assert frames[2]["presented_frame_gap_from_previous"] == 1


def test_current_mode_has_zero_pending_encoder_queue():
    case = {
        "video_path": "x/y.MOV", "label": "y", "timer_ticks": 2,
        "browser_total_video_frames": 2, "browser_dropped_video_frames": 0,
        "frames": [
            {"video_time_seconds": 0.0, "presented_frames": 1, "callback_time_ms": 1.0},
            {"video_time_seconds": 0.04, "presented_frames": 2, "callback_time_ms": 41.0},
        ],
    }
    summary, _ = summarize_clip("current_40ms", case, case["frames"], [0.0, 0.04], {"source_pts_step_median_seconds": 0.04})
    assert summary["max_pending_encodes"] == 0
    assert summary["unseen_presented_frames"] == 0


def test_phase7_capture_page_contains_both_policies_and_keeps_draw_inside_callback():
    page = (Path(__file__).resolve().parents[1] / "scripts" / "browser_validation_capture_phase7.html").read_text(encoding="utf-8")
    assert "requestVideoFrameCallback" in page
    assert "metadata.mediaTime" in page
    assert "context.drawImage(video" in page
    assert "MIN_INTERVAL_MS = 39" in page
    assert "canvas.toDataURL('image/jpeg'" in page
    assert "canvas.toBlob(async blob" in page
    assert "pending_encodes_at_enqueue" in page
    assert "presented_frames" in page
    assert "setInterval(" not in page


def test_phase7_server_is_loopback_only_and_separates_modes():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "serve_phase7_capture.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", args.port)' in source
    assert 'choices=sorted(MODES)' in source
    assert '"phase7_mode": self.capture_mode' in source


def test_mode_a_and_mode_b_are_saved_in_distinct_capture_directories():
    runner = (Path(__file__).resolve().parents[1] / "scripts" / "run_phase7_capture_completeness.ps1").read_text(encoding="utf-8")
    assert 'phase7_capture_current_40ms_' in runner
    assert 'phase7_capture_every_presented_frame_' in runner
    assert "--mode', (ConvertTo-ProcessArgument $Mode)" in runner
    assert "'--mode-a-dir', $ModeAPath" in runner
    assert "'--mode-b-dir', $ModeBPath" in runner


def test_phase7_header_extras_remain_compatible_with_audited_capture_protocol():
    from browser_capture_protocol import encode_capture, parse_capture_payload

    config = {
        "canvas_width": 640,
        "jpeg_quality": 0.92,
        "timer_interval_ms": 40,
        "max_capture_seconds": 6.0,
        "max_frames": 169,
        "diagnostic_mode": True,
        "iot_enabled": False,
        "iot_command_emission": False,
        "classifier_input_mirrored": False,
        "capture_sampling_mode": "requestVideoFrameCallback",
        "capture_timestamp_field": "metadata.mediaTime",
        "draw_synchronous_inside_callback": True,
        "phase7_mode": "every_presented_frame",
        "phase7_every_presented_frame": True,
        "phase7_encoding": "asynchronous_toBlob",
    }
    frame_meta = [
        {"video_time_seconds": round(0.04 * (i + 1), 3), "elapsed_ms": 40.0 * (i + 1), "width": 640, "height": 360, "jpeg_bytes": 4,
         "presented_frames": i + 2, "callback_work_ms": 0.5, "encode_latency_ms": 3.0}
        for i in range(12)
    ]
    case = {
        "video_path": "Greetings/demo.MOV", "label": "hello", "source_name": "demo.MOV",
        "source_width": 640, "source_height": 360, "capture_duration_ms": 500.0,
        "frame_count": 12, "timer_ticks": 12, "duplicate_frame_skips": 0,
        "browser_total_video_frames": 13, "browser_dropped_video_frames": 0,
        "frames": frame_meta, "phase7_mode": "every_presented_frame",
    }
    payload = encode_capture({"schema_version": 1, "capture_config": config, "case": case}, [b"\xff\xd8\xff\xd9"] * 12)
    parsed = parse_capture_payload(payload, {"video_path": "Greetings/demo.MOV", "label": "hello"})
    assert len(parsed.frames) == 12
    assert parsed.header["case"]["phase7_mode"] == "every_presented_frame"


def test_load_capture_checks_mode_header_not_timestamped_directory_name(tmp_path):
    import hashlib
    from browser_capture_protocol import encode_capture
    from phase7_capture_completeness import load_capture

    mode_dir = tmp_path / "phase7_capture_every_presented_frame_20261107_220000"
    clips_dir = mode_dir / "clips"
    clips_dir.mkdir(parents=True)
    config = {
        "canvas_width": 640, "jpeg_quality": 0.92, "timer_interval_ms": 40,
        "max_capture_seconds": 6.0, "max_frames": 169, "diagnostic_mode": True,
        "iot_enabled": False, "iot_command_emission": False, "classifier_input_mirrored": False,
        "capture_sampling_mode": "requestVideoFrameCallback", "capture_timestamp_field": "metadata.mediaTime",
        "draw_synchronous_inside_callback": True, "phase7_mode": "every_presented_frame",
        "phase7_every_presented_frame": True, "phase7_encoding": "asynchronous_toBlob",
    }
    jpeg = b"\xff\xd8\xff\xd9"
    frames = [
        {"video_time_seconds": round(0.04 * (i + 1), 3), "elapsed_ms": 40.0 * (i + 1),
         "width": 640, "height": 360, "jpeg_bytes": 4, "presented_frames": i + 2}
        for i in range(12)
    ]
    case = {"video_path": "Greetings/demo.MOV", "label": "hello", "source_name": "demo.MOV",
            "source_width": 640, "source_height": 360, "capture_duration_ms": 500.0,
            "frame_count": 12, "timer_ticks": 12, "duplicate_frame_skips": 0,
            "browser_total_video_frames": 13, "browser_dropped_video_frames": 0,
            "frames": frames, "phase7_mode": "every_presented_frame"}
    payload = encode_capture({"schema_version": 1, "capture_config": config, "case": case}, [jpeg] * 12)
    path = clips_dir / "demo.islbrw"
    path.write_bytes(payload)
    record = {"video_path": "Greetings/demo.MOV", "container": "clips/demo.islbrw",
              "sha256": hashlib.sha256(payload).hexdigest()}
    loaded_case, loaded_frames, loaded_hash = load_capture(
        mode_dir, "every_presented_frame", {"video_path": "Greetings/demo.MOV", "label": "hello"},
        {"greetings/demo.mov": record},
    )
    assert loaded_case["phase7_mode"] == "every_presented_frame"
    assert len(loaded_frames) == 12
    assert loaded_hash == record["sha256"]


def test_load_capture_rejects_wrong_mode_even_when_container_is_valid(tmp_path):
    import hashlib
    from browser_capture_protocol import encode_capture
    from phase7_capture_completeness import load_capture

    mode_dir = tmp_path / "capture_dir_name_does_not_determine_mode"
    clips_dir = mode_dir / "clips"
    clips_dir.mkdir(parents=True)
    config = {
        "canvas_width": 640, "jpeg_quality": 0.92, "timer_interval_ms": 40,
        "max_capture_seconds": 6.0, "max_frames": 169, "diagnostic_mode": True,
        "iot_enabled": False, "iot_command_emission": False, "classifier_input_mirrored": False,
        "capture_sampling_mode": "requestVideoFrameCallback", "capture_timestamp_field": "metadata.mediaTime",
        "draw_synchronous_inside_callback": True, "phase7_mode": "current_40ms",
    }
    frame_meta = [{"video_time_seconds": i * 0.04, "elapsed_ms": i * 40.0, "width": 640, "height": 360, "jpeg_bytes": 4}
                  for i in range(12)]
    case = {"video_path": "Greetings/demo.MOV", "label": "hello", "source_name": "demo.MOV",
            "source_width": 640, "source_height": 360, "capture_duration_ms": 500.0,
            "frame_count": 12, "timer_ticks": 12, "duplicate_frame_skips": 0,
            "browser_total_video_frames": 12, "browser_dropped_video_frames": 0,
            "frames": frame_meta, "phase7_mode": "current_40ms"}
    payload = encode_capture({"schema_version": 1, "capture_config": config, "case": case}, [b"\xff\xd8\xff\xd9"] * 12)
    (clips_dir / "demo.islbrw").write_bytes(payload)
    record = {"video_path": "Greetings/demo.MOV", "container": "clips/demo.islbrw", "sha256": hashlib.sha256(payload).hexdigest()}
    with pytest.raises(ValueError, match="Capture mode mismatch"):
        load_capture(mode_dir, "every_presented_frame", {"video_path": "Greetings/demo.MOV", "label": "hello"}, {"greetings/demo.mov": record})


def test_phase7_launcher_uses_standalone_python_version_probe():
    project = Path(__file__).resolve().parents[1]
    runner = (project / "scripts" / "run_phase7_capture_completeness.ps1").read_text(encoding="utf-8-sig")
    probe = project / "scripts" / "phase7_python_version.py"
    assert probe.is_file()
    assert "$VersionScript = Join-Path $ProjectRoot 'scripts\\phase7_python_version.py'" in runner
    assert "& $Python $VersionScript *> $VersionLog" in runner
    assert "-c $versionCode" not in runner
