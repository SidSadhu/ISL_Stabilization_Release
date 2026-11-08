from __future__ import annotations

import re
from pathlib import Path

from scripts.phase10_browser_lifecycle_analysis import (
    classify_mode_exposure,
    focus_recovery_latencies,
    focus_state_counts,
    pair_event_durations,
    summarize_frames,
    visibility_hidden_durations,
)

ROOT = Path(__file__).resolve().parents[1]


def test_focus_state_counts_separates_visible_unfocused_from_hidden():
    frames = [
        {"capture_kind": "rvfc_playback", "visibility_state": "visible", "has_focus": True},
        {"capture_kind": "rvfc_playback", "visibility_state": "visible", "has_focus": False},
        {"capture_kind": "rvfc_playback", "visibility_state": "hidden", "has_focus": False},
        {"capture_kind": "initial_paused", "visibility_state": "visible", "has_focus": True},
    ]
    assert focus_state_counts(frames) == {
        "callback_frames": 3,
        "callbacks_visible_focused": 1,
        "callbacks_visible_unfocused": 1,
        "callbacks_hidden": 1,
        "callbacks_other_or_unknown": 0,
    }


def test_pair_event_durations_matches_blur_to_focus_in_order():
    timeline = [
        {"event_type": "window_blur", "elapsed_ms": 100},
        {"event_type": "window_blur", "elapsed_ms": 250},
        {"event_type": "window_focus", "elapsed_ms": 400},
        {"event_type": "window_focus", "elapsed_ms": 700},
    ]
    assert pair_event_durations(timeline, "window_blur", "window_focus") == [300.0, 450.0]


def test_focus_recovery_latency_measures_next_callback_after_focus():
    timeline = [
        {"event_type": "window_focus", "elapsed_ms": 20},  # initial focus is not a recovery
        {"event_type": "window_blur", "elapsed_ms": 100},
        {"event_type": "window_focus", "elapsed_ms": 500},
        {"event_type": "rvfc_delivered", "elapsed_ms": 532},
        {"event_type": "window_focus", "elapsed_ms": 800},  # unmatched focus is ignored
        {"event_type": "rvfc_delivered", "elapsed_ms": 850},
    ]
    assert focus_recovery_latencies(timeline) == [32.0]


def test_mode_exposure_validation_for_all_conditions():
    counts = {"callbacks_visible_focused": 8, "callbacks_visible_unfocused": 2, "callbacks_hidden": 0}
    focused_counts = {"callbacks_visible_focused": 8, "callbacks_visible_unfocused": 0, "callbacks_hidden": 0}
    assert classify_mode_exposure("foreground_focused", event_types=[], focus_counts=focused_counts, marker=None,
                                  initial_visibility="visible", initial_focus=True)[0]
    assert classify_mode_exposure("visible_unfocused", event_types=["window_blur"], focus_counts=counts, marker=None,
                                  blur_visible_count=1)[0]
    assert classify_mode_exposure("hidden_background", event_types=["document_visibilitychange"], focus_counts=counts, marker=None,
                                  hidden_transition_count=1)[0]
    assert classify_mode_exposure(
        "focus_recovery", event_types=["window_blur", "window_focus"], focus_counts=counts,
        marker={"trusted": True},
    )[0]


def test_mode_exposure_does_not_overclaim_when_condition_missing():
    empty = {"callbacks_visible_focused": 0, "callbacks_visible_unfocused": 0, "callbacks_hidden": 0}
    assert not classify_mode_exposure("visible_unfocused", event_types=[], focus_counts=empty, marker=None)[0]
    assert not classify_mode_exposure("hidden_background", event_types=[], focus_counts=empty, marker=None)[0]
    assert not classify_mode_exposure("focus_recovery", event_types=["window_blur"], focus_counts=empty, marker=None)[0]


def test_summarize_frames_maps_paused_initial_and_reports_interior_gaps():
    pts = [0.0, 0.04, 0.08, 0.12, 0.16]
    frames = [
        {"capture_kind": "initial_paused", "video_time_seconds": 0.0},
        {"capture_kind": "rvfc_playback", "video_time_seconds": 0.04, "presented_frames": 2, "callback_time_ms": 40},
        {"capture_kind": "rvfc_playback", "video_time_seconds": 0.12, "presented_frames": 4, "callback_time_ms": 120},
        {"capture_kind": "rvfc_playback", "video_time_seconds": 0.16, "presented_frames": 5, "callback_time_ms": 160},
    ]
    result = summarize_frames(pts, frames)
    assert result["initial_paused_records"] == 1
    assert result["first_record_is_initial_paused"] is True
    assert result["initial_frame_maps_to_source_zero"] is True
    assert result["unique_source_frames_captured"] == 4
    assert result["missing_source_frame_ranges"] == [[2, 2]]
    assert result["missing_interior_frame_count"] == 1


def test_browser_template_has_safe_unquoted_mode_tokens_and_all_instructions():
    html = (ROOT / "scripts" / "browser_validation_capture_phase10.html").read_text(encoding="utf-8")
    assert "const MODE = __PHASE10_MODE__;" in html
    assert "const RUN_ID = __PHASE10_RUN_ID__;" in html
    assert "'__PHASE10_MODE__'" not in html
    assert "visible_unfocused" in html
    assert "hidden_background" in html
    assert "focus_recovery" in html
    assert "controlled_recovery_interaction" in html


def test_capture_configuration_is_diagnostic_and_uses_sync_encoder():
    html = (ROOT / "scripts" / "browser_validation_capture_phase10.html").read_text(encoding="utf-8")
    assert "const ENCODER = 'sync'" in html
    assert "callback_gate_ms: 0" in html
    assert "iot_enabled: false" in html
    assert "classifier_checkpoint_loaded" not in html or "No classifier" in html
    assert "toDataURL('image/jpeg'" in html


def test_phase10_analysis_and_orchestrator_compile_as_normal_python_files():
    import ast
    for name in ("phase10_browser_lifecycle_analysis.py", "phase10_browser_lifecycle_orchestrator.py", "serve_phase10_capture.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        ast.parse(source, filename=name)



def test_phase10_receiver_injects_unquoted_mode_identity(tmp_path):
    import threading
    import urllib.request
    from scripts.serve_phase10_capture import Phase10CaptureServer, Phase10Handler

    server = Phase10CaptureServer(
        ("127.0.0.1", 0), Phase10Handler, rows=[], output_dir=tmp_path,
        overwrite=False, max_upload_bytes=2_000_000,
        mode="hidden_background", run_id="hidden_background",
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert 'const MODE = "hidden_background";' in page
        assert 'const RUN_ID = "hidden_background";' in page
        assert "__PHASE10_MODE__" not in page
        assert "__PHASE10_RUN_ID__" not in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_phase10_receiver_is_loopback_only():
    source = (ROOT / "scripts" / "serve_phase10_capture.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", args.port)' in source
    assert 'choices=MODES' in source


def test_phase10_receiver_accepts_only_matching_mode_identity(tmp_path):
    import json
    import threading
    import urllib.request
    from browser_capture_protocol import EXPECTED_CONFIG, encode_capture
    from scripts.serve_phase10_capture import Phase10CaptureServer, Phase10Handler

    expected = {"video_path": "Example/01. Demo/demo.mov", "label": "hello"}
    mode = "foreground_focused"
    run_id = mode
    server = Phase10CaptureServer(
        ("127.0.0.1", 0), Phase10Handler, rows=[expected], output_dir=tmp_path,
        overwrite=False, max_upload_bytes=2_000_000, mode=mode, run_id=run_id,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        frames = [b"\xff\xd8\x00\x01\xff\xd9" for _ in range(12)]
        metadata = []
        for index in range(12):
            metadata.append({
                "video_time_seconds": index * 0.04,
                "elapsed_ms": index * 40.0,
                "width": 640,
                "height": 360,
                "capture_kind": "initial_paused" if index == 0 else "rvfc_playback",
                "jpeg_bytes": len(frames[index]),
            })
        case = {
            **expected,
            "source_name": "demo.mov",
            "source_width": 640,
            "source_height": 360,
            "frame_count": 12,
            "frames": metadata,
            "timer_ticks": 12,
            "duplicate_frame_skips": 0,
            "browser_total_video_frames": 12,
            "browser_dropped_video_frames": 0,
            "capture_duration_ms": 500.0,
            "phase10_mode": mode,
            "phase10_run_id": run_id,
            "phase10_capture_encoder": "sync",
            "phase10_initial_frame_policy": "paused_draw_encode_before_play",
        }
        config = {
            **EXPECTED_CONFIG,
            "phase10_mode": mode,
            "phase10_run_id": run_id,
            "phase10_capture_encoder": "sync",
        }
        payload = encode_capture({"schema_version": 1, "capture_config": config, "case": case}, frames)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/capture",
            data=payload,
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
        assert body["status"] == "saved"
        saved_index = json.loads((tmp_path / "capture_index.json").read_text(encoding="utf-8"))
        assert saved_index["received_count"] == 1
        assert saved_index["capture_config"]["phase10_mode"] == mode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_visibility_hidden_durations_uses_actual_visibilitychange_events():
    timeline = [
        {"event_type": "document_visibilitychange", "elapsed_ms": 100, "visibility_state_after": "hidden"},
        {"event_type": "document_visibilitychange", "elapsed_ms": 8100, "visibility_state_after": "visible"},
        {"event_type": "document_visibilitychange", "elapsed_ms": 9000, "visibility_state_after": "hidden"},
    ]
    assert visibility_hidden_durations(timeline) == [8000.0]


def test_phase10_orchestrator_defines_bundled_modes_from_capture_passes():
    from scripts.phase10_browser_lifecycle_orchestrator import MODES, PASS_SPECS
    assert MODES == tuple(mode for mode, _port in PASS_SPECS)
    assert len(MODES) == 4
