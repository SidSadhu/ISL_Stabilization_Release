from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_validation_parity_batch.py"
spec = importlib.util.spec_from_file_location("run_validation_parity_batch", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_select_cases_includes_all_errors_and_same_class_control_first():
    rows = [
        {"status": "success", "label": "boy", "prediction": "girl", "confidence": "0.31", "top5_predictions": "girl|boy", "video_path": "people/boy1.mov"},
        {"status": "success", "label": "boy", "prediction": "girl", "confidence": "0.34", "top5_predictions": "girl|boy", "video_path": "people/boy2.mov"},
        {"status": "success", "label": "boy", "prediction": "boy", "confidence": "0.82", "top5_predictions": "boy|girl", "video_path": "people/boy3.mov"},
        {"status": "success", "label": "car", "prediction": "car", "confidence": "0.94", "top5_predictions": "car|bus", "video_path": "cars/car1.mov"},
        {"status": "failed", "label": "girl", "prediction": "", "confidence": "", "top5_predictions": "", "video_path": "people/girl1.mov"},
    ]
    cases = module.select_cases(rows, max_errors=0, controls=1)
    assert len(cases) == 3
    assert [case["case_type"] for case in cases] == ["error", "error", "control"]
    assert [case["video_path"] for case in cases[:2]] == ["people/boy1.mov", "people/boy2.mov"]
    assert cases[2]["video_path"] == "people/boy3.mov"


def test_select_cases_falls_back_to_global_controls_when_no_same_class_control():
    rows = [
        {"status": "success", "label": "dog", "prediction": "dry", "confidence": "0.20", "video_path": "animals/dog.mov"},
        {"status": "success", "label": "bird", "prediction": "bird", "confidence": "0.95", "video_path": "animals/bird.mov"},
    ]
    cases = module.select_cases(rows, controls=1)
    assert len(cases) == 2
    assert cases[1]["case_type"] == "control"
    assert cases[1]["label"] == "bird"


def test_summary_compares_prediction_paths_and_feature_difference():
    cases = [
        {"case_type": "error", "label": "boy", "baseline_prediction": "girl", "baseline_confidence": 0.3, "video_path": "boy.mov"},
        {"case_type": "control", "label": "car", "baseline_prediction": "car", "baseline_confidence": 0.9, "video_path": "car.mov"},
    ]
    reports = [
        {
            "status": "success",
            "app_pipeline_prediction_same_rng_seed": {"top1_label": "girl"},
            "official_pipeline_prediction_same_rng_seed": {"top1_label": "boy"},
            "feature_parity": {"mean_absolute_feature_difference": 10.0, "p95_absolute_feature_difference": 100.0, "exactly_equal": False},
        },
        {
            "status": "success",
            "app_pipeline_prediction_same_rng_seed": {"top1_label": "car"},
            "official_pipeline_prediction_same_rng_seed": {"top1_label": "car"},
            "feature_parity": {"mean_absolute_feature_difference": 2.0, "p95_absolute_feature_difference": 20.0, "exactly_equal": False},
        },
    ]
    summary = module.summarize_reports(cases, reports)
    assert summary["successful_diagnostic_count"] == 2
    assert summary["same_top1_between_paths"] == 1
    assert summary["app_path_top1_correct_count"] == 1
    assert summary["reference_path_top1_correct_count"] == 2
    assert summary["median_mean_absolute_feature_difference"] == 6.0


def test_summary_discloses_historical_pose_compatibility_limitation():
    summary = module.summarize_reports([], [])
    assert "not historical upper_body_only" in summary["warning"]
