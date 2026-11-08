"""
test_preprocessing_contract.py — Exhaustive model input contract tests.

Covers:
  - Exact shape (169, 134), finite, float32
  - Zero padding correctness
  - Truncation of overlength sequences
  - Interpolation with missing frames
  - All-pose-missing, one-hand-missing, both-hands-missing
  - Scaling: x*1920, y*1080
  - Hand slot assignment
  - NaN/Inf rejection
  - Deterministic repeated inference (dropout=off)
"""
import math
import numpy as np
import pytest

from isl_recognition.preprocess import (
    make_scaled_frame_feature,
    preprocess_feature_sequence,
    interpolate_and_scale,
    PreprocessError,
)
from isl_recognition.landmarks import landmark_results_to_feature_frame


def _zeros_frame():
    return make_scaled_frame_feature(
        [0.0]*25, [0.0]*25,
        [0.0]*21, [0.0]*21,
        [0.0]*21, [0.0]*21,
    )


class TestFrameShape:
    def test_shape_is_134(self):
        vec = _zeros_frame()
        assert vec.shape == (134,)

    def test_dtype_float32(self):
        vec = _zeros_frame()
        assert vec.dtype == np.float32

    def test_scaling_x_1920(self):
        vec = make_scaled_frame_feature(
            [0.1]*25, [0.0]*25,
            [0.0]*21, [0.0]*21,
            [0.0]*21, [0.0]*21,
        )
        assert abs(vec[0] - 0.1 * 1920.0) < 1e-3

    def test_scaling_y_1080(self):
        vec = make_scaled_frame_feature(
            [0.0]*25, [0.2]*25,
            [0.0]*21, [0.0]*21,
            [0.0]*21, [0.0]*21,
        )
        assert abs(vec[1] - 0.2 * 1080.0) < 1e-3

    def test_concat_order_pose_hand1_hand2(self):
        """Feature order: 50 pose floats, 42 hand1 floats, 42 hand2 floats."""
        vec = make_scaled_frame_feature(
            [0.1]*25, [0.0]*25,
            [0.2]*21, [0.0]*21,
            [0.3]*21, [0.0]*21,
        )
        # Pose: indices 0-49, hand1: 50-91, hand2: 92-133
        assert abs(vec[0] - 0.1 * 1920.0) < 1e-3    # pose_x[0]
        assert abs(vec[50] - 0.2 * 1920.0) < 1e-3   # hand1_x[0]
        assert abs(vec[92] - 0.3 * 1920.0) < 1e-3   # hand2_x[0]

    def test_inf_raises(self):
        with pytest.raises(PreprocessError):
            make_scaled_frame_feature(
                [float('inf')]*25, [0.0]*25,
                [0.0]*21, [0.0]*21,
                [0.0]*21, [0.0]*21,
                allow_nan=False,
            )

    def test_nan_allowed_with_flag(self):
        vec = make_scaled_frame_feature(
            [float('nan')]*25, [0.0]*25,
            [0.0]*21, [0.0]*21,
            [0.0]*21, [0.0]*21,
            allow_nan=True,
        )
        assert vec.shape == (134,)


class TestSequencePadding:
    def test_short_sequence_padded(self):
        seq = np.ones((30, 134), dtype=np.float32)
        raw, padded = preprocess_feature_sequence(seq)
        assert raw.shape == (30, 134)
        assert padded.shape == (169, 134)
        assert np.array_equal(padded[:30], seq)
        assert np.all(padded[30:] == 0)

    def test_exactly_169_no_padding(self):
        seq = np.ones((169, 134), dtype=np.float32)
        raw, padded = preprocess_feature_sequence(seq)
        assert raw.shape == (169, 134)
        assert np.all(padded == 1.0)

    def test_overlength_truncated_to_169(self):
        seq = np.arange(200*134, dtype=np.float32).reshape(200, 134)
        raw, padded = preprocess_feature_sequence(seq)
        assert raw.shape == (169, 134)
        assert padded.shape == (169, 134)
        assert np.array_equal(padded, seq[:169])

    def test_single_frame_padded(self):
        seq = np.ones((1, 134), dtype=np.float32) * 5.0
        _, padded = preprocess_feature_sequence(seq)
        assert padded.shape == (169, 134)
        assert np.all(padded[0] == 5.0)
        assert np.all(padded[1:] == 0.0)

    def test_output_is_finite(self):
        seq = np.random.rand(61, 134).astype(np.float32)
        _, padded = preprocess_feature_sequence(seq)
        assert np.isfinite(padded).all()


class TestInterpolation:
    def test_nan_in_middle_interpolated(self):
        seq = np.array([[0.0]*134, [np.nan]*134, [2.0]*134], dtype=np.float32)
        raw, padded = preprocess_feature_sequence(seq)
        assert np.allclose(raw[1], 1.0), "Middle NaN should be interpolated to 1.0"
        assert np.isfinite(padded).all()

    def test_all_nan_column_becomes_zero(self):
        seq = np.ones((10, 134), dtype=np.float32)
        seq[:, 5] = np.nan
        _, padded = preprocess_feature_sequence(seq)
        assert np.isfinite(padded).all()
        assert np.all(padded[:, 5] == 0.0)

    def test_leading_nan_extrapolated(self):
        seq = np.array([[np.nan]*134, [1.0]*134, [2.0]*134], dtype=np.float32)
        raw, _ = preprocess_feature_sequence(seq)
        assert np.isfinite(raw).all()

    def test_trailing_nan_extrapolated(self):
        seq = np.array([[1.0]*134, [2.0]*134, [np.nan]*134], dtype=np.float32)
        raw, _ = preprocess_feature_sequence(seq)
        assert np.isfinite(raw).all()


class TestMissingLandmarks:
    def test_both_hands_missing_gives_zero_hand_features(self):
        """Both hands missing: hand1 and hand2 features should be 0 after interpolation."""
        pose_x = [0.5] * 25
        pose_y = [0.5] * 25
        h1_x = [math.nan] * 21
        h1_y = [math.nan] * 21
        h2_x = [math.nan] * 21
        h2_y = [math.nan] * 21
        frames = []
        for _ in range(30):
            vec = make_scaled_frame_feature(
                pose_x, pose_y, h1_x, h1_y, h2_x, h2_y, allow_nan=True
            )
            frames.append(vec)
        seq = np.stack(frames).astype(np.float32)
        _, padded = preprocess_feature_sequence(seq)
        assert np.isfinite(padded).all()
        # Hand features (indices 50-133) should be 0 after all-NaN interpolation
        assert np.all(padded[:30, 50:] == 0.0)

    def test_pose_missing_gives_zero_pose_features(self):
        frames = []
        for _ in range(10):
            vec = make_scaled_frame_feature(
                [math.nan]*25, [math.nan]*25,
                [0.5]*21, [0.5]*21,
                [math.nan]*21, [math.nan]*21,
                allow_nan=True,
            )
            frames.append(vec)
        seq = np.stack(frames).astype(np.float32)
        _, padded = preprocess_feature_sequence(seq)
        assert np.isfinite(padded).all()
        assert np.all(padded[:10, :50] == 0.0)

    def test_one_hand_missing_only_fills_that_slot(self):
        frames = []
        for _ in range(15):
            vec = make_scaled_frame_feature(
                [0.3]*25, [0.4]*25,
                [0.6]*21, [0.7]*21,      # hand1 present
                [math.nan]*21, [math.nan]*21,  # hand2 missing
                allow_nan=True,
            )
            frames.append(vec)
        seq = np.stack(frames).astype(np.float32)
        _, padded = preprocess_feature_sequence(seq)
        assert np.isfinite(padded).all()
        # hand1 (indices 50-91) should not be zero
        assert not np.all(padded[:15, 50:92] == 0.0)
        # hand2 (indices 92-133) should be zero (all-NaN column → 0)
        assert np.all(padded[:15, 92:] == 0.0)


class TestDeterminism:
    def test_identical_input_gives_identical_output(self):
        """Preprocessing must be deterministic for the same input."""
        seq = np.random.RandomState(42).rand(50, 134).astype(np.float32)
        _, p1 = preprocess_feature_sequence(seq.copy())
        _, p2 = preprocess_feature_sequence(seq.copy())
        assert np.array_equal(p1, p2)
