from __future__ import annotations

from types import SimpleNamespace
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cv2
import numpy as np

from phase4_controlled_replay_diagnostics import (
    VARIANTS,
    _comparison,
    _labels_and_scores,
    _prepare_frame,
)


def test_phase4_variants_are_unique_and_cover_transport_and_tracking_axes():
    names = [row["name"] for row in VARIANTS]
    assert len(names) == len(set(names)) == 7
    assert "browser_canvas_tracking" in names
    assert "browser_canvas_static" in names
    assert "native_resized_png_tracking" in names
    assert "native_resized_jpeg_q92_tracking" in names
    assert "native_resized_jpeg_q92_static" in names


def test_native_png_and_jpeg_paths_use_exact_canvas_dimensions():
    source = np.zeros((90, 160, 3), dtype=np.uint8)
    source[:, :, 1] = 180
    for mode in ("native_png", "native_jpeg92"):
        image = _prepare_frame(source, mode, None, (64, 36), cv2, np)
        assert image.shape == (36, 64, 3)
        assert image.dtype == np.uint8


def test_browser_path_decodes_the_exact_jpeg_payload():
    source = np.full((36, 64, 3), 127, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", source, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    assert ok
    image = _prepare_frame(None, "browser_jpeg", encoded.tobytes(), (64, 36), cv2, np)
    assert image.shape == source.shape


def test_native_original_path_does_not_resize_source():
    source = np.zeros((90, 160, 3), dtype=np.uint8)
    image = _prepare_frame(source, "native_original", None, (64, 36), cv2, np)
    assert image is source
    assert image.shape == (90, 160, 3)


def test_ordered_and_unordered_handedness_comparisons_are_separate():
    baseline = {
        "variant": "browser_canvas_tracking",
        "frames": [
            {"detected_hand_count": 2, "handedness_labels_in_detector_order": "Right|Left",
             "handedness_labels_as_unordered_set": "Left|Right"},
            {"detected_hand_count": 1, "handedness_labels_in_detector_order": "Left",
             "handedness_labels_as_unordered_set": "Left"},
        ],
    }
    other = {
        "variant": "native_original_tracking",
        "frames": [
            {"detected_hand_count": 2, "handedness_labels_in_detector_order": "Left|Right",
             "handedness_labels_as_unordered_set": "Left|Right"},
            {"detected_hand_count": 1, "handedness_labels_in_detector_order": "Left",
             "handedness_labels_as_unordered_set": "Left"},
        ],
    }
    result = _comparison(baseline, other, "example.mov")
    assert result["same_hand_count_frames"] == 2
    assert result["ordered_label_match_frames"] == 1
    assert result["unordered_label_match_frames"] == 2


def test_handedness_scores_are_retained_without_assuming_labels():
    values = [
        SimpleNamespace(classification=[SimpleNamespace(label="Left", score=0.987)]),
        SimpleNamespace(classification=[SimpleNamespace(label="Right", score=0.951)]),
    ]
    labels, scores = _labels_and_scores(values)
    assert labels == ["Left", "Right"]
    assert scores == [0.987, 0.951]


def test_no_hand_pairs_are_excluded_from_label_comparability():
    baseline = {
        "variant": "browser_canvas_tracking",
        "frames": [
            {"detected_hand_count": 0, "handedness_labels_in_detector_order": "",
             "handedness_labels_as_unordered_set": ""},
            {"detected_hand_count": 1, "handedness_labels_in_detector_order": "Left",
             "handedness_labels_as_unordered_set": "Left"},
        ],
    }
    other = {
        "variant": "native_original_tracking",
        "frames": [
            {"detected_hand_count": 0, "handedness_labels_in_detector_order": "",
             "handedness_labels_as_unordered_set": ""},
            {"detected_hand_count": 1, "handedness_labels_in_detector_order": "Right",
             "handedness_labels_as_unordered_set": "Right"},
        ],
    }
    result = _comparison(baseline, other, "example.mov")
    assert result["same_hand_count_frames"] == 2
    assert result["label_comparable_frames_same_count"] == 1
    assert result["ordered_label_match_frames"] == 0
