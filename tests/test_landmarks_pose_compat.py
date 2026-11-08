"""
test_landmarks_pose_compat.py — Verify the modern Pose output's INCLUDE 25-slot adapter.

These tests verify index selection and the downstream feature shape only. They do NOT
prove coordinate-level equivalence with MediaPipe's removed historical upper_body_only
model used by the original INCLUDE preprocessing pipeline.

Tests verify:
  1. extract_upper_body_pose returns exactly 25 points from a 33-point result.
  2. The extracted points match the first 25 raw landmarks (not a permuted subset).
  3. Missing/empty pose result returns NaN lists.
  4. Wrist indices 15/16 used for hand assignment match BlazePose-33 wrist positions.
  5. Full landmark_results_to_feature_frame produces valid 134-D feature.
  6. Lower-body landmarks (indices 25-32) are excluded.
"""
import math
from types import SimpleNamespace
import numpy as np
import pytest

from isl_recognition.landmarks import (
    UPPER_BODY_INDICES,
    LEFT_WRIST_IDX,
    RIGHT_WRIST_IDX,
    extract_upper_body_pose,
    landmark_results_to_feature_frame,
    assign_hands,
)
from isl_recognition.preprocess import make_scaled_frame_feature


def _make_pose_result(n=33):
    """Fake MediaPipe Pose result with n distinct landmarks."""
    lms = [SimpleNamespace(x=float(i)*0.01, y=float(i)*0.01+0.001, z=0.0, visibility=1.0)
           for i in range(n)]
    return SimpleNamespace(pose_landmarks=SimpleNamespace(landmark=lms))


def _make_hands_result(n_hands=0):
    if n_hands == 0:
        return SimpleNamespace(multi_hand_landmarks=None, multi_handedness=None)
    lms = [SimpleNamespace(x=0.5+i*0.01, y=0.5, z=0.0) for i in range(21)]
    hands = [SimpleNamespace(landmark=lms) for _ in range(n_hands)]
    return SimpleNamespace(multi_hand_landmarks=hands, multi_handedness=None)


class TestUpperBodyPoseExtraction:
    def test_returns_exactly_25_points(self):
        pose = _make_pose_result(33)
        x, y, found = extract_upper_body_pose(pose)
        assert len(x) == 25
        assert len(y) == 25
        assert found is True

    def test_first_25_indices_are_the_expected_include_slots(self):
        """The adapter selects 0-24 to preserve the expected INCLUDE 25-slot layout."""
        assert UPPER_BODY_INDICES == list(range(25))

    def test_extracted_values_match_indices_0_to_24(self):
        pose = _make_pose_result(33)
        x, y, _ = extract_upper_body_pose(pose)
        raw = pose.pose_landmarks.landmark
        for i in range(25):
            assert abs(x[i] - raw[i].x) < 1e-6, f"Index {i} x mismatch"
            assert abs(y[i] - raw[i].y) < 1e-6, f"Index {i} y mismatch"

    def test_lower_body_indices_excluded(self):
        pose = _make_pose_result(33)
        x, y, _ = extract_upper_body_pose(pose)
        raw = pose.pose_landmarks.landmark
        # Lower-body: indices 25-32 should NOT appear in extracted x/y
        lower_body_x = {raw[i].x for i in range(25, 33)}
        extracted_x = set(x)
        assert lower_body_x.isdisjoint(extracted_x), "Lower-body landmarks leaked into extraction"

    def test_missing_pose_returns_nan(self):
        empty = SimpleNamespace(pose_landmarks=None)
        x, y, found = extract_upper_body_pose(empty)
        assert found is False
        assert len(x) == 25 and all(math.isnan(v) for v in x)
        assert len(y) == 25 and all(math.isnan(v) for v in y)

    def test_none_pose_returns_nan(self):
        x, y, found = extract_upper_body_pose(None)
        assert found is False
        assert all(math.isnan(v) for v in x)

    def test_wrist_indices_correct(self):
        """The existing INCLUDE one-hand placement uses pose indices 15 and 16."""
        # In the current Pose landmark order, indices 15 and 16 are wrist slots.
        assert LEFT_WRIST_IDX == 15
        assert RIGHT_WRIST_IDX == 16
        # Both are within the 25-point upper-body set
        assert LEFT_WRIST_IDX < 25
        assert RIGHT_WRIST_IDX < 25


class TestLandmarkFrameToFeature:
    def test_valid_pose_no_hands_produces_134d(self):
        pose = _make_pose_result(33)
        hands = _make_hands_result(0)
        frame = landmark_results_to_feature_frame(hands, pose)
        assert len(frame.pose_x) == 25
        assert len(frame.hand1_x) == 21
        assert frame.hand_count == 0
        # Build feature vector
        vec = make_scaled_frame_feature(
            frame.pose_x, frame.pose_y,
            frame.hand1_x, frame.hand1_y,
            frame.hand2_x, frame.hand2_y,
            allow_nan=True,
        )
        assert vec.shape == (134,)
        assert not np.isinf(vec).any()

    def test_no_pose_no_hands_all_nan(self):
        pose = SimpleNamespace(pose_landmarks=None)
        hands = _make_hands_result(0)
        frame = landmark_results_to_feature_frame(hands, pose)
        assert all(math.isnan(v) for v in frame.pose_x)
        assert frame.pose_found is False
        assert frame.hand_count == 0

    def test_one_hand_assigned_via_wrist_proximity(self):
        """Single detected hand should be placed by wrist proximity."""
        # Create pose with left wrist at (0.2, 0.5) and right at (0.8, 0.5)
        lms = [SimpleNamespace(x=0.5, y=0.5, z=0.0, visibility=1.0) for _ in range(33)]
        lms[15] = SimpleNamespace(x=0.2, y=0.5, z=0.0, visibility=1.0)  # left_wrist
        lms[16] = SimpleNamespace(x=0.8, y=0.5, z=0.0, visibility=1.0)  # right_wrist
        pose = SimpleNamespace(pose_landmarks=SimpleNamespace(landmark=lms))
        # Hand at x=0.82 — should be assigned to slot 2 (right)
        hand_lms = [SimpleNamespace(x=0.82, y=0.5, z=0.0) for _ in range(21)]
        hands = SimpleNamespace(
            multi_hand_landmarks=[SimpleNamespace(landmark=hand_lms)],
            multi_handedness=None,
        )
        frame = landmark_results_to_feature_frame(hands, pose)
        # Hand should be in slot 2 (right side), slot 1 should be NaN
        assert all(math.isnan(v) for v in frame.hand1_x), "Hand 1 should be NaN (right-side hand)"
        assert not any(math.isnan(v) for v in frame.hand2_x), "Hand 2 should have valid coords"


class TestPreprocessIntegration:
    def test_full_pipeline_shape_and_finite(self):
        """End-to-end: pose + hands → LandmarkFrame → 134-D → sequence → (169,134)."""
        from isl_recognition.preprocess import preprocess_feature_sequence
        pose = _make_pose_result(33)
        hands = _make_hands_result(1)
        frame = landmark_results_to_feature_frame(hands, pose)
        vec = make_scaled_frame_feature(
            frame.pose_x, frame.pose_y,
            frame.hand1_x, frame.hand1_y,
            frame.hand2_x, frame.hand2_y,
            allow_nan=True,
        )
        # Simulate a 30-frame sequence
        frames = np.tile(vec, (30, 1)).astype(np.float32)
        _, padded = preprocess_feature_sequence(frames)
        assert padded.shape == (169, 134)
        assert np.isfinite(padded).all()
