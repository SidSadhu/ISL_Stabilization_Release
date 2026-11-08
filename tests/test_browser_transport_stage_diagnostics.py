from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_browser_transport_stages.py"
spec = importlib.util.spec_from_file_location("diagnose_browser_transport_stages", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_resized_dimensions_preserve_aspect_ratio():
    assert module.resized_dimensions(1280, 720, 640) == (640, 360)
    assert module.resized_dimensions(1920, 1080, 640) == (640, 360)
    assert module.resized_dimensions(640, 480, 640) == (640, 480)


@pytest.mark.parametrize("args", [(0, 720, 640), (1280, 0, 640), (1280, 720, 0)])
def test_resized_dimensions_reject_invalid_inputs(args):
    with pytest.raises(ValueError):
        module.resized_dimensions(*args)


def test_encode_variants_produces_png_and_two_jpegs():
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :320] = (20, 100, 220)
    resized, variants, pixel_diffs = module.encode_frame_variants(frame, cv2, 640, (72, 92))

    assert resized.shape == (480, 640, 3)
    assert set(variants) == {
        "resized_640_png", "resized_640_jpeg_q72", "resized_640_jpeg_q92"
    }
    assert pixel_diffs["resized_640_png"] == 0.0
    assert pixel_diffs["resized_640_jpeg_q72"] >= 0.0
    assert pixel_diffs["resized_640_jpeg_q92"] >= 0.0


def test_encode_variants_resizes_to_width_640():
    cv2 = pytest.importorskip("cv2")
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    resized, variants, _ = module.encode_frame_variants(frame, cv2, 640, (72, 92))
    assert resized.shape == (360, 640, 3)


def test_encode_variants_rejects_non_image():
    cv2 = pytest.importorskip("cv2")
    with pytest.raises(ValueError):
        module.encode_frame_variants(np.zeros((10, 10), dtype=np.uint8), cv2, 640, (72, 92))
