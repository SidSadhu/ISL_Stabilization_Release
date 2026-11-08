from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from diagnose_feature_pipeline_stages import (  # noqa: E402
    feature_group_stats,
    feature_presence,
    presence_summary,
    presence_disagreement,
    compare_feature_sequences,
)


def test_feature_group_stats_reports_threshold_fractions():
    left = np.zeros((2, 4), dtype=np.float32)
    right = np.array([[0, 2, 20, 200], [0, 0, 0, 0]], dtype=np.float32)
    stats = feature_group_stats(left, right)
    assert stats["mean_absolute_difference"] == pytest.approx(27.75)
    assert stats["max_absolute_difference"] == pytest.approx(200.0)
    assert stats["fraction_over_1"] == pytest.approx(3 / 8)
    assert stats["fraction_over_10"] == pytest.approx(2 / 8)
    assert stats["fraction_over_100"] == pytest.approx(1 / 8)


def test_presence_summary_detects_hand_slots_before_interpolation():
    features = np.full((3, 134), np.nan, dtype=np.float32)
    # All three frames have pose.
    features[:, 0:50] = 1
    # hand1 detected in frames 0 and 1.
    features[0:2, 50:92] = 1
    # hand2 detected only in frame 1.
    features[1, 92:134] = 1
    summary = presence_summary(features)
    assert summary["pose"] == pytest.approx(1.0)
    assert summary["hand1"] == pytest.approx(2 / 3)
    assert summary["hand2"] == pytest.approx(1 / 3)
    assert summary["any_hand"] == pytest.approx(2 / 3)
    assert summary["both_hands"] == pytest.approx(1 / 3)


def test_presence_disagreement_counts_per_group():
    left = np.full((2, 134), np.nan, dtype=np.float32)
    right = np.full((2, 134), np.nan, dtype=np.float32)
    left[:, 0:50] = 1
    right[0, 0:50] = 1
    left[0, 50:92] = 1
    right[1, 50:92] = 1
    report = presence_disagreement(left, right)
    assert report["pose"]["mismatched_frames"] == 1
    assert report["hand1"]["mismatched_frames"] == 2
    assert report["hand2"]["mismatched_frames"] == 0


def test_compare_feature_sequences_splits_pose_and_hands():
    left = np.zeros((3, 134), dtype=np.float32)
    right = np.zeros_like(left)
    right[:, 0:50] = 2
    right[:, 50:92] = 4
    right[:, 92:134] = 6
    report = compare_feature_sequences(left, right)
    assert report["groups"]["pose"]["mean_absolute_difference"] == pytest.approx(2.0)
    assert report["groups"]["hand1"]["mean_absolute_difference"] == pytest.approx(4.0)
    assert report["groups"]["hand2"]["mean_absolute_difference"] == pytest.approx(6.0)
    assert len(report["top_frames_by_mean_difference"]) == 3
    assert report["groups"]["hand2"]["largest_dimensions"][0]["coordinate"] in ("x", "y")


def test_presence_rejects_invalid_feature_shape():
    with pytest.raises(ValueError):
        feature_presence(np.zeros((10, 100), dtype=np.float32))
