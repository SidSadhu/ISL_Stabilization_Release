from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from diagnose_browser_transport_stages import encode_frame_variants, resized_dimensions


def test_resized_dimensions_preserve_aspect_ratio_with_integer_rounding():
    assert resized_dimensions(1920, 1080, 640) == (640, 360)
    assert resized_dimensions(1920, 1088, 640) == (640, 363)


def test_phase2_transport_variants_only_include_lossless_png_and_production_q92():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    _, encoded, pixel_diffs = encode_frame_variants(frame, cv2, 640, (92,))

    assert set(encoded) == {"resized_640_png", "resized_640_jpeg_q92"}
    assert set(pixel_diffs) == set(encoded)
    assert "resized_640_jpeg_q72" not in encoded

    for name, payload in encoded.items():
        decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape == (360, 640, 3)
        assert np.isfinite(pixel_diffs[name])
