from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_phase2_feature_provenance_77 import aggregate, metric, safe_stem


def _report(label: str, expected: str, predictions: dict, mad: float = 2.0) -> dict:
    return {
        "status": "success",
        "video_path": f"Adjectives/{label}.MOV",
        "expected_label": expected,
        "predictions_same_seed": predictions,
        "feature_comparisons_after_interpolation": {
            "app_direct_native_vs_resized_png": {
                "groups": {g: {"mean_absolute_difference": mad} for g in ("pose", "hand1", "hand2")}
            },
            "resized_png_vs_jpeg_q92": {
                "groups": {g: {"mean_absolute_difference": mad / 2} for g in ("pose", "hand1", "hand2")}
            },
            "app_direct_native_vs_upstream": {
                "groups": {g: {"mean_absolute_difference": mad * 1.5} for g in ("pose", "hand1", "hand2")}
            },
            "opencv_q92_vs_upstream": {
                "groups": {g: {"mean_absolute_difference": mad * 2} for g in ("pose", "hand1", "hand2")}
            },
        },
        "top1_agreement_by_pair": {
            "app_direct_native_vs_resized_png": True,
            "resized_png_vs_jpeg_q92": True,
            "app_direct_native_vs_upstream": False,
            "opencv_q92_vs_upstream": False,
        },
    }


def test_metric_handles_empty_and_numeric_values():
    assert metric([])["count"] == 0
    assert metric([1, 2, 3])["mean"] == 2.0
    assert metric([1, 2, 3])["median"] == 2.0


def test_safe_stem_is_stable_and_filesystem_safe():
    assert safe_stem(4, "small little", "Adjectives/84. small little/MVI 12.MOV") == "04_small_little_MVI_12"


def test_aggregate_computes_accuracy_and_pair_agreements():
    preds = {
        "app_direct_native": {"top1_label": "hello", "top1_confidence": 0.8, "top5": [{"label": "hello"}]},
        "resized_640_png": {"top1_label": "hello", "top1_confidence": 0.7, "top5": [{"label": "hello"}]},
        "resized_640_jpeg_q92": {"top1_label": "hello", "top1_confidence": 0.6, "top5": [{"label": "hello"}]},
        "upstream_original": {"top1_label": "good", "top1_confidence": 0.5, "top5": [{"label": "good"}, {"label": "hello"}]},
    }
    result = aggregate([_report("one", "hello", preds), _report("two", "hello", preds, mad=4.0)])
    assert result["successful_count"] == 2
    assert result["recognition_by_variant"]["app_direct_native"]["top1_accuracy"] == 1.0
    assert result["recognition_by_variant"]["upstream_original"]["top1_accuracy"] == 0.0
    assert result["top1_agreement_counts"]["native_vs_resized_png"] == 2
    assert result["top1_agreement_counts"]["app_native_vs_upstream_modern_adapter"] == 0
    assert len(result["per_clip"]) == 2


def test_diagnostic_result_requires_four_complete_variants_and_locked_input_shape():
    from run_phase2_feature_provenance_77 import VARIANTS, validate_diagnostic_result

    payload = {
        "status": "success",
        "expected_label": "hello",
        "model_input_shape": [169, 134],
        "frame_counts_by_path": {name: 40 for name in VARIANTS},
        "predictions_same_seed": {
            name: {
                "top1_label": "hello",
                "top1_confidence": 0.8,
                "top5": [{"label": f"label{i}"} for i in range(5)],
            }
            for name in VARIANTS
        },
    }
    validate_diagnostic_result(payload, "hello")

    payload["frame_counts_by_path"]["resized_640_jpeg_q92"] = 39
    try:
        validate_diagnostic_result(payload, "hello")
    except RuntimeError as exc:
        assert "frame counts differ" in str(exc)
    else:
        raise AssertionError("Expected mismatched frame paths to fail closed")
