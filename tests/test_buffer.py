import numpy as np

from isl_recognition.buffer import TemporalBuffer


def test_buffer_zero_pads_at_end():
    b = TemporalBuffer(5, 2)
    for i in range(2):
        b.append(np.array([i, i+10], dtype=np.float32))
    x = b.padded()
    assert x.shape == (5,2)
    assert np.array_equal(x[:2], np.array([[0,10],[1,11]], dtype=np.float32))
    assert np.all(x[2:] == 0)
