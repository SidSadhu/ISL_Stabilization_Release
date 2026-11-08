from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_phase2_ablation_report import VARIANTS, build_report


def test_build_report_requires_complete_77_clip_run(tmp_path):
    summary = {"status": "incomplete", "successful_count": 76, "failed_count": 1, "selected_count": 77}
    frame = pd.DataFrame({"video_path": [f"v{i}.MOV" for i in range(77)]})
    try:
        build_report(summary, frame, tmp_path)
    except ValueError as exc:
        assert "completed with 77" in str(exc)
    else:
        raise AssertionError("Incomplete Phase 2 run must not be reported as complete")


def test_build_report_creates_complete_ablation_outputs(tmp_path):
    labels = [f"class{i % 50}" for i in range(77)]
    frame_data = {
        "video_path": [f"group/v{i}.MOV" for i in range(77)],
        "expected_label": labels,
        "status": ["success"] * 77,
    }
    for variant in VARIANTS:
        frame_data[f"{variant}_top1"] = labels
        frame_data[f"{variant}_confidence"] = [0.8] * 77
        frame_data[f"{variant}_correct"] = [True] * 77
    frame = pd.DataFrame(frame_data)

    groups = {name: {"mean": 1.0, "median": 1.0, "p90": 2.0, "max": 3.0} for name in ("pose", "hand1", "hand2")}
    pair_names = (
        "native_vs_resized_png",
        "resize_png_vs_jpeg_q92",
        "app_native_vs_upstream_modern_adapter",
        "opencv_q92_vs_upstream_modern_adapter",
    )
    summary = {
        "status": "completed",
        "successful_count": 77,
        "failed_count": 0,
        "selected_count": 77,
        "runtime": {"python": "3.12"},
        "assets": {"checkpoint_sha256": "abc"},
        "methodology": {},
        "limitations": [],
        "recognition_by_variant": {
            v: {
                "predicted_count": 77,
                "top1_correct_count": 77,
                "top1_accuracy": 1.0,
                "top5_correct_count": 77,
                "top5_accuracy": 1.0,
                "per_class": {},
                "confidence": {},
            }
            for v in VARIANTS
        },
        "feature_mad_by_pair_and_group": {p: groups for p in pair_names},
        "top1_agreement_counts": {p: 77 for p in pair_names},
    }

    result = build_report(summary, frame, tmp_path)
    assert result["selected_count"] == 77
    assert (tmp_path / "PHASE2_ABLATION_REPORT.md").is_file()
    assert (tmp_path / "phase2_variant_accuracy.png").is_file()
    assert (tmp_path / "phase2_pair_comparisons.csv").is_file()
