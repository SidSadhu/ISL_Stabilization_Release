from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "phase6_rvfc_alignment_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("phase6_rvfc_alignment_diagnostics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_nearest_pts_index_exact_and_between_frames():
    index, delta = MODULE.nearest_pts_index(0.041, [0.0, 0.04, 0.08])
    assert index == 1
    assert abs(delta + 0.001) < 1e-9
    index, delta = MODULE.nearest_pts_index(0.06, [0.0, 0.04, 0.08])
    assert index == 1
    assert abs(delta + 0.02) < 1e-9


def test_nearest_pts_index_uses_first_index_for_exact_tie():
    index, delta = MODULE.nearest_pts_index(0.02, [0.0, 0.04])
    assert index == 0
    assert abs(delta + 0.02) < 1e-9


def test_timing_summary_tracks_current_time_and_presented_frame_gaps():
    rows = [
        {"video_time_seconds": 0.04, "capture_current_time_seconds": 0.041,
         "presented_frames": 2, "callback_time_ms": 100.0},
        {"video_time_seconds": 0.08, "capture_current_time_seconds": 0.082,
         "presented_frames": 3, "callback_time_ms": 140.0},
        {"video_time_seconds": 0.16, "capture_current_time_seconds": 0.161,
         "presented_frames": 5, "callback_time_ms": 220.0},
    ]
    result = MODULE.summarize_timing(rows)
    assert result["frame_count"] == 3
    assert result["media_time_monotonic"] is True
    assert result["presented_frames_monotonic"] is True
    assert result["presented_frame_gap_count_gt_one"] == 1
    assert result["presented_frame_total_gap"] == 1
    assert abs(result["current_time_minus_media_time_mean_ms"] - 1.3333333333) < 1e-6
    assert result["callback_gap_median_ms"] == 60.0


def test_timing_summary_empty_rows_is_safe():
    result = MODULE.summarize_timing([])
    assert result["frame_count"] == 0
    assert result["current_time_minus_media_time_mean_ms"] is None
    assert result["callback_gap_median_ms"] is None


def test_detector_pair_reports_ordered_and_unordered_separately():
    rows = []
    def add(variant, frame, labels, count=2):
        rows.append({
            "video_path": "x.mov", "variant": variant, "frame_index": frame,
            "detected_hand_count": count,
            "handedness_labels_in_detector_order": labels,
            "handedness_labels_as_unordered_set": "|".join(sorted(labels.split("|"))) if labels else "",
        })
    add("browser_canvas", 0, "Right|Left")
    add("browser_canvas", 1, "Left|Right")
    add("native_pts_area_jpeg92", 0, "Left|Right")
    add("native_pts_area_jpeg92", 1, "Left|Right")
    add("native_pixel_best", 0, "Right|Left")
    add("native_pixel_best", 1, "Right|Left")
    pairs = MODULE._detector_pairs(rows)
    area = next(x for x in pairs if x["comparison_variant"] == "native_pts_area_jpeg92")
    assert area["ordered_label_match_frames"] == 1
    assert area["unordered_label_match_frames"] == 2


def test_rvfc_capture_page_draws_inside_callback_and_uses_media_time():
    page = (Path(__file__).resolve().parents[1] / "scripts" / "browser_validation_capture_rvfc.html").read_text(encoding="utf-8")
    assert "requestVideoFrameCallback" in page
    assert "metadata.mediaTime" in page
    assert "context.drawImage(video" in page
    assert "setInterval(" not in page
    assert "capture_current_time_seconds" in page
    assert "presented_frames" in page
    assert "capture_timestamp_field: 'metadata.mediaTime'" in page
    assert "draw_synchronous_inside_callback: true" in page


def test_server_is_loopback_only_and_uses_separate_phase6_page():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "serve_phase6_rvfc_capture.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", args.port)' in source
    assert 'browser_validation_capture_rvfc.html' in source
    assert 'ISL_IOT_ENABLED' in source
    assert 'class Phase6CaptureServer(CaptureServer)' in source
    assert 'capture_sampling_mode": "requestVideoFrameCallback"' in source
