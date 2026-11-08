from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from diagnose_reference_parity import install_pose_compatibility  # noqa: E402


class _ModernPose:
    def __init__(self, min_detection_confidence=0.5, min_tracking_confidence=0.5):
        self.closed = False

    def process(self, image):
        return SimpleNamespace(pose_landmarks=SimpleNamespace(landmark=list(range(33))))

    def close(self):
        self.closed = True


class _LegacyPose:
    def __init__(self, min_detection_confidence=0.5, min_tracking_confidence=0.5, upper_body_only=False):
        self.upper_body_only = upper_body_only


def test_modern_pose_adapter_accepts_legacy_keyword_and_returns_25_points():
    mp = SimpleNamespace(solutions=SimpleNamespace(pose=SimpleNamespace(Pose=_ModernPose)))
    mode = install_pose_compatibility(mp)
    assert mode == "compat_full_pose_first_25_points_not_exact_legacy_model"
    pose = mp.solutions.pose.Pose(upper_body_only=True, min_detection_confidence=0.5)
    result = pose.process(object())
    assert len(result.pose_landmarks.landmark) == 25
    pose.close()


def test_modern_pose_adapter_preserves_full_pose_when_not_requested():
    mp = SimpleNamespace(solutions=SimpleNamespace(pose=SimpleNamespace(Pose=_ModernPose)))
    install_pose_compatibility(mp)
    pose = mp.solutions.pose.Pose()
    result = pose.process(object())
    assert len(result.pose_landmarks.landmark) == 33


def test_legacy_pose_class_is_left_unchanged():
    mp = SimpleNamespace(solutions=SimpleNamespace(pose=SimpleNamespace(Pose=_LegacyPose)))
    mode = install_pose_compatibility(mp)
    assert mode == "native_legacy_upper_body_only_supported"
    pose = mp.solutions.pose.Pose(upper_body_only=True)
    assert pose.upper_body_only is True
