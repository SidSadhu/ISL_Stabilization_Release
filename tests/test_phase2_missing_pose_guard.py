from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from diagnose_browser_transport_stages import _install_upstream_missing_pose_guard
from run_phase2_feature_provenance_77 import (
    PRIOR_76_RUN_DIAGNOSTIC_SHA256,
    PRIOR_76_RUNNER_SHA256,
    compatible_prior_pose_guard_result,
)


def test_upstream_missing_pose_is_25_nan_slots_and_counted():
    calls = []

    def original(results):
        calls.append(results)
        return [0.25] * 25, [0.75] * 25

    module = SimpleNamespace(process_pose_keypoints=original)
    stats = _install_upstream_missing_pose_guard(module)

    x, y = module.process_pose_keypoints(SimpleNamespace(pose_landmarks=None))

    assert len(x) == len(y) == 25
    assert all(np.isnan(x))
    assert all(np.isnan(y))
    assert stats["missing_pose_frame_count"] == 1
    assert calls == []


def test_upstream_missing_pose_guard_preserves_existing_pose_path():
    sentinel = object()
    calls = []

    def original(results):
        calls.append(results)
        return [0.1] * 25, [0.2] * 25

    module = SimpleNamespace(process_pose_keypoints=original)
    stats = _install_upstream_missing_pose_guard(module)
    x, y = module.process_pose_keypoints(SimpleNamespace(pose_landmarks=sentinel))

    assert x == [0.1] * 25
    assert y == [0.2] * 25
    assert calls and calls[0].pose_landmarks is sentinel
    assert stats["missing_pose_frame_count"] == 0


def _prior_cached_result(current_fingerprint: dict) -> dict:
    prior_fingerprint = __import__("json").loads(__import__("json").dumps(current_fingerprint))
    prior_fingerprint["source_file_sha256"]["phase2_diagnostic"] = PRIOR_76_RUN_DIAGNOSTIC_SHA256
    prior_fingerprint["source_file_sha256"]["phase2_runner"] = PRIOR_76_RUNNER_SHA256
    return {
        "status": "success",
        "video_path": "Adjectives/quiet/MVI_9537.MOV",
        "expected_label": "quiet",
        "phase2_provenance": {
            "runner_fingerprint": prior_fingerprint,
            "fingerprint": "old-fingerprint",
            "source_video_sha256": "source-hash",
            "official_video_path": "Adjectives/quiet/MVI_9537.MOV",
            "expected_label": "quiet",
        },
    }


def test_resume_compatibility_accepts_only_exact_known_script_migration():
    current = {
        "campaign": "phase2_feature_provenance_77_v2",
        "repo_commit": "pinned-commit",
        "source_file_sha256": {
            "phase2_diagnostic": "new-diagnostic-hash",
            "phase2_runner": "new-runner-hash",
            "app_feature_extractor": "unchanged-app-hash",
        },
        "runtime": {"python": "3.12.10", "mediapipe": "0.10.21"},
        "model_contract": {"classes": 50, "features": 134, "frames": 169},
    }
    cached = _prior_cached_result(current)

    assert compatible_prior_pose_guard_result(
        cached,
        current,
        source_video_sha256="source-hash",
        official_video_path="Adjectives/quiet/MVI_9537.MOV",
        expected_label="quiet",
    )

    assert not compatible_prior_pose_guard_result(
        cached,
        current,
        source_video_sha256="different-source-hash",
        official_video_path="Adjectives/quiet/MVI_9537.MOV",
        expected_label="quiet",
    )

    changed_app = __import__("json").loads(__import__("json").dumps(current))
    changed_app["source_file_sha256"]["app_feature_extractor"] = "changed-app-hash"
    assert not compatible_prior_pose_guard_result(
        cached,
        changed_app,
        source_video_sha256="source-hash",
        official_video_path="Adjectives/quiet/MVI_9537.MOV",
        expected_label="quiet",
    )


def test_resume_compatibility_rejects_unknown_prior_code_hashes():
    current = {
        "source_file_sha256": {
            "phase2_diagnostic": "new-diagnostic-hash",
            "phase2_runner": "new-runner-hash",
        }
    }
    cached = _prior_cached_result(current)
    cached["phase2_provenance"]["runner_fingerprint"]["source_file_sha256"]["phase2_runner"] = "unrecognized-old-runner"

    assert not compatible_prior_pose_guard_result(
        cached,
        current,
        source_video_sha256="source-hash",
        official_video_path="Adjectives/quiet/MVI_9537.MOV",
        expected_label="quiet",
    )
