from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from phase5_canvas_pixel_audit import (  # noqa: E402
    CODECS,
    DETECTOR_VARIANTS,
    INTERPOLATIONS,
    OFFSETS,
    _aggregate_pixel_rows,
    pixel_metrics,
    resize_roundtrip,
)


def test_pixel_metrics_are_zero_for_identical_frames():
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    result = pixel_metrics(image, image.copy())
    assert result["mae_bgr"] == 0
    assert result["rmse_bgr"] == 0
    assert result["psnr_db"] == 99
    assert result["bias_b"] == result["bias_g"] == result["bias_r"] == 0


def test_pixel_metrics_reject_shape_and_dtype_mismatches():
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="shape mismatch"):
        pixel_metrics(image, np.zeros((12, 16, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="uint8"):
        pixel_metrics(image.astype(np.float32), image.astype(np.float32))


def test_pixel_metrics_signed_bias_direction_is_consistent():
    reference = np.full((8, 8, 3), 100, dtype=np.uint8)
    candidate = np.full((8, 8, 3), 110, dtype=np.uint8)
    result = pixel_metrics(reference, candidate)
    assert result["mae_bgr"] == 10
    assert result["bias_b"] == result["bias_g"] == result["bias_r"] == 10


def test_resize_roundtrip_preserves_requested_dimensions_for_every_filter_codec():
    source = np.zeros((90, 160, 3), dtype=np.uint8)
    source[:, :, 1] = 170
    for name in INTERPOLATIONS:
        for codec in CODECS:
            result = resize_roundtrip(source, (64, 36), name, codec, cv2)
            assert result.shape == (36, 64, 3)
            assert result.dtype == np.uint8


def test_resize_roundtrip_rejects_unknown_filter_and_codec():
    source = np.zeros((10, 10, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="Unsupported interpolation"):
        resize_roundtrip(source, (5, 5), "unknown", "png", cv2)
    with pytest.raises(ValueError, match="Unsupported codec"):
        resize_roundtrip(source, (5, 5), "linear", "webp", cv2)


def test_phase5_offsets_and_detector_variants_are_complete_and_unique():
    assert OFFSETS == (-1, 0, 1)
    assert set(INTERPOLATIONS) == {"nearest", "linear", "area", "cubic", "lanczos4"}
    assert CODECS == ("png", "jpeg92")
    names = [row["name"] for row in DETECTOR_VARIANTS]
    assert len(names) == len(set(names)) == 7
    assert names[0] == "browser_canvas_static"
    assert all(name.endswith("_static") for name in names)


def test_aggregate_pixel_rows_separates_clip_filter_codec_and_offset():
    rows = [
        {"video_path": "a.mov", "interpolation": "linear", "codec": "png", "source_frame_offset": 0,
         "mae_bgr": 2.0, "rmse_bgr": 3.0, "psnr_db": 38.0},
        {"video_path": "a.mov", "interpolation": "linear", "codec": "png", "source_frame_offset": 0,
         "mae_bgr": 4.0, "rmse_bgr": 5.0, "psnr_db": 34.0},
        {"video_path": "a.mov", "interpolation": "linear", "codec": "jpeg92", "source_frame_offset": 0,
         "mae_bgr": 6.0, "rmse_bgr": 7.0, "psnr_db": 31.0},
    ]
    result = _aggregate_pixel_rows(rows)
    assert len(result) == 2
    png = next(row for row in result if row["codec"] == "png")
    assert png["frame_count"] == 2
    assert png["mean_mae_bgr"] == 3.0
    assert png["median_mae_bgr"] == 3.0


def test_pixel_audit_emits_filter_codec_and_neighbor_offset_rows():
    from types import SimpleNamespace
    from phase5_canvas_pixel_audit import _pixel_audit_for_clip

    source = np.zeros((48, 80, 3), dtype=np.uint8)
    source[:, :, 1] = 120
    ok, encoded = cv2.imencode('.jpg', cv2.resize(source, (64, 38)), [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    assert ok
    parsed = SimpleNamespace(
        frames=(encoded.tobytes(),),
        header={"case": {"frames": [{"video_time_seconds": 0.04}]}},
    )
    sources = {
        0: np.full_like(source, 30),
        1: source,
        2: np.full_like(source, 210),
    }
    rows, best = _pixel_audit_for_clip('sample.mov', parsed, sources, [1], (64, 38), cv2)
    assert len(rows) == 5 * 2 * 3
    assert len(best) == 1
    assert {row['source_frame_offset'] for row in rows} == {-1, 0, 1}
    assert {row['interpolation'] for row in rows} == set(INTERPOLATIONS)
    assert {row['codec'] for row in rows} == set(CODECS)
    assert best[0]['best_source_frame_offset'] == 0
