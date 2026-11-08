from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import phase3_hand_assignment_diagnostics as phase3


def test_norm_path_canonicalizes_windows_paths():
    assert phase3.norm_path(r"Greetings\51. Good Morning\MVI_0099.MOV") == "greetings/51. good morning/mvi_0099.mov"


def test_summarize_interpolation_reports_missing_features_and_group():
    values = np.zeros((3, 134), dtype=np.float32)
    values[1, 50:92] = np.nan
    report = phase3.summarize_interpolation(values)
    assert report["frame_count"] == 3
    assert report["missing_coordinate_count_before_interpolation"] == 42
    assert report["groups"]["hand1"]["frames_with_any_missing_coordinate"] == 1
    assert report["groups"]["pose"]["missing_coordinate_count"] == 0
    assert report["frames_with_all_features_finite_after_interpolation"] == 3


def test_summarize_interpolation_preserves_entirely_missing_hand_as_zero_after_fill():
    values = np.zeros((4, 134), dtype=np.float32)
    values[:, 92:134] = np.nan
    report = phase3.summarize_interpolation(values)
    assert report["groups"]["hand2"]["missing_coordinate_count"] == 168
    assert report["imputed_coordinate_count"] == 168


def test_compare_handedness_excludes_empty_frames_from_label_fraction():
    browser = [
        {"detected_hand_count": 0, "handedness_labels_in_detector_order": "", "slot_assignment_changed_by_wrist_nearest": False},
        {"detected_hand_count": 1, "handedness_labels_in_detector_order": "Left", "slot_assignment_changed_by_wrist_nearest": False},
        {"detected_hand_count": 2, "handedness_labels_in_detector_order": "Left|Right", "slot_assignment_changed_by_wrist_nearest": True},
    ]
    native = [
        {"detected_hand_count": 0, "handedness_labels_in_detector_order": "", "slot_assignment_changed_by_wrist_nearest": False},
        {"detected_hand_count": 1, "handedness_labels_in_detector_order": "Right", "slot_assignment_changed_by_wrist_nearest": False},
        {"detected_hand_count": 2, "handedness_labels_in_detector_order": "Left|Right", "slot_assignment_changed_by_wrist_nearest": False},
    ]
    result = phase3.compare_handedness(browser, native)
    assert result["paired_frame_count"] == 3
    assert result["handedness_label_comparable_frames"] == 2
    assert result["same_ordered_handedness_label_frames"] == 1
    assert result["same_ordered_handedness_label_fraction_when_comparable"] == 0.5


def test_capture_target_set_has_four_outliers_and_two_controls():
    assert len(phase3.TARGETS) == 6
    roles = [value for value in phase3.TARGETS.values()]
    assert sum("control" in role for role in roles) == 2
    assert sum("outlier" in role or "recovered" in role for role in roles) == 4


def test_timestamp_source_index_rounding_contract():
    # Browser frames are associated with the nearest decoded source frame.
    fps = 30.0
    for timestamp in (0.04, 0.08, 0.12, 1.0, 2.34):
        index = max(0, int(__import__("math").floor(timestamp * fps + 0.5)))
        assert abs(index / fps - timestamp) <= 0.5 / fps + 1e-9


def test_report_builds_complete_table_and_preserves_interpretation_guardrails():
    sample = {
        "status": "completed", "successful_clip_count": 1, "selected_clip_count": 1,
        "official_validation_split_sha256": "abc",
        "clips": [{
            "video_path": "Greetings/51. Good Morning/MVI_0099.MOV",
            "expected_label": "goodmorning", "diagnostic_role": "slot outlier; test",
            "browser_detection_presence": {"any_hand_frames": 4, "two_hand_frames": 2},
            "native_detection_presence": {"any_hand_frames": 5, "two_hand_frames": 3},
            "paired_detector_comparison": {
                "same_detected_hand_count_frames": 8, "paired_frame_count": 10,
                "same_ordered_handedness_label_frames": 6, "handedness_label_comparable_frames": 7,
            },
            "browser_plus_native_frames_with_slot_counterfactual_change": 3,
            "wrist_normalized_distance_mean": 0.01,
        }],
    }
    report = phase3.build_report(sample)
    assert "MVI_0099.MOV" in report
    assert "physical" in phase3.__doc__ and "mirror-aware calibration" in phase3.__doc__
    assert "wrist_nearest" in report


def test_terminal_browser_timestamp_holds_final_decoded_source_frame():
    # 56 frames at 25 fps have valid indices 0..55 and a nominal duration of 2.24s.
    # Browser currentTime can reach 2.24s while the decoder still displays frame 55.
    assert phase3._nearest_source_frame_target(2.24, 25.0, 56) == (55, True)


def test_timestamp_within_last_frame_interval_uses_last_frame_without_hold():
    assert phase3._nearest_source_frame_target(2.20, 25.0, 56) == (55, False)


def test_timestamp_beyond_terminal_half_frame_is_rejected():
    with pytest.raises(RuntimeError, match="exceeds the decodable video interval"):
        phase3._nearest_source_frame_target(2.261, 25.0, 56)


def test_out_of_range_frame_count_or_fps_is_rejected():
    with pytest.raises(ValueError, match="Invalid source video metadata"):
        phase3._nearest_source_frame_target(0.1, 0.0, 56)


class _FakeCapture:
    def __init__(self, frame_count: int):
        self.frame_count = frame_count
        self.position = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == 5:  # CAP_PROP_FPS in the fake cv2 below
            return 25.0
        if prop == 7:  # CAP_PROP_FRAME_COUNT
            return float(self.frame_count)
        return 0.0

    def read(self):
        if self.position >= self.frame_count:
            return False, None
        frame = np.full((2, 2, 3), self.position, dtype=np.uint8)
        self.position += 1
        return True, frame

    def release(self):
        self.released = True


class _FakeCV2:
    CAP_PROP_FPS = 5
    CAP_PROP_FRAME_COUNT = 7

    def __init__(self, frame_count: int):
        self.capture = _FakeCapture(frame_count)

    def VideoCapture(self, _path):
        return self.capture


def test_source_decoder_maps_terminal_timestamp_to_last_frame_and_flags_it(tmp_path):
    fake_cv2 = _FakeCV2(frame_count=56)
    frames, meta = phase3._timestamp_matched_source(
        tmp_path / "short_clip.mp4", [0.0, 2.20, 2.24], fake_cv2
    )
    assert len(frames) == 3
    assert meta["frame_indices"] == [0, 55, 55]
    assert meta["terminal_frame_hold_flags"] == [False, False, True]
    assert meta["terminal_frame_hold_count"] == 1
    assert meta["terminal_frame_hold_browser_timestamps_seconds"] == [2.24]
    assert meta["max_terminal_alignment_delta_ms"] == pytest.approx(40.0)
    assert fake_cv2.capture.released is True
