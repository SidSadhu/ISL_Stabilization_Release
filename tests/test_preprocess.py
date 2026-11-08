import numpy as np

from isl_recognition.preprocess import (
    interpolate_and_scale,
    make_scaled_frame_feature,
    preprocess_feature_sequence,
    preprocess_record,
)


def test_interleave_and_scale_shape():
    frame = make_scaled_frame_feature(
        [0.1]*25, [0.2]*25,
        [0.3]*21, [0.4]*21,
        [0.5]*21, [0.6]*21,
    )
    assert frame.shape == (134,)
    assert np.allclose(frame[0], 192.0)
    assert np.allclose(frame[1], 216.0)


def test_sequence_padding():
    seq = np.arange(61*134, dtype=np.float32).reshape(61, 134)
    raw, padded = preprocess_feature_sequence(seq)
    assert raw.shape == (61, 134)
    assert padded.shape == (169, 134)
    assert np.array_equal(padded[:61], seq)
    assert np.all(padded[61:] == 0)


def test_record_preprocessing_handles_nans():
    def arr(n, fill):
        data = [[fill]*n for _ in range(3)]
        data[1][0] = np.nan
        return data
    row = {
        "pose_x": arr(25, 0.1), "pose_y": arr(25, 0.2),
        "hand1_x": arr(21, 0.3), "hand1_y": arr(21, 0.4),
        "hand2_x": [[np.nan]*21]*3, "hand2_y": [[np.nan]*21]*3,
    }
    raw, padded = preprocess_record(row)
    assert raw.shape == (3, 134)
    assert padded.shape == (169, 134)
    assert np.isfinite(raw).all()
    assert np.all(raw[:,92:] == 0)


def test_live_sequence_interpolation():
    seq = np.array([[0.0]*134, [np.nan]*134, [2.0]*134], dtype=np.float32)
    raw, padded = preprocess_feature_sequence(seq)
    assert np.allclose(raw[1], 1.0)
    assert np.isfinite(padded).all()
