from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_phase2_completed_results.py"

spec = importlib.util.spec_from_file_location("repair_phase2_completed_results", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_official_video_path_takes_precedence_over_local_extension_variant():
    item = {
        "video": r"C:\AIAssets\INCLUDE_videos\Jobs\91. Priest\MVI_8888.MP4",
        "video_path": "Jobs/91. Priest/MVI_8888.MOV",
    }
    assert module.individual_video_identity(item) == "jobs/91. priest/mvi_8888.mov"


def test_phase2_report_explicitly_labels_first_attempt_log_historical():
    report = module.build_report({
        "generated_at_utc":"2026-11-07T13:43:22Z", "elapsed_seconds":1,
        "assets":{"repo_commit":module.EXPECTED_REPO_COMMIT,
                  "official_split_sha256":module.EXPECTED_OFFICIAL_SPLIT_SHA,
                  "checkpoint_sha256":module.EXPECTED_CHECKPOINT_SHA},
        "resume_compatibility":{"prior_successes_reused":76},
        "recognition_by_variant":{}, "top1_agreement_counts":{},
        "feature_mad_by_pair_and_group":{}, "methodology":{}, "limitations":[]
    }, None)
    assert "76` previously successful clips reused" in report
    assert "not treated as the final successful run log" in report
    assert "77/77 official validation clips; 0 failed" in report
