import numpy as np

from isl_recognition.stabilizer import PredictionStabilizer


def test_threshold_stabilization():
    s = PredictionStabilizer(mode="threshold", confidence_threshold=0.8)
    out = s.update(np.array([0.9, 0.1], dtype=np.float32), ["court", "happy"])
    assert out.label == "court"
    assert out.is_stable


def test_low_confidence_is_not_stable():
    s = PredictionStabilizer(mode="threshold", confidence_threshold=0.8)
    out = s.update(np.array([0.6, 0.4], dtype=np.float32), ["court", "happy"])
    assert out.label is None
    assert not out.is_stable
