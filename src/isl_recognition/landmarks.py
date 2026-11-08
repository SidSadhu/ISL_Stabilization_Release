from __future__ import annotations

"""
landmarks.py — MediaPipe landmark extraction and INCLUDE 25-point slot mapping.

IMPORTANT SOURCE-PARITY LIMITATION
----------------------------------
The official AI4Bharat INCLUDE keypoint generator calls MediaPipe Pose with
upper_body_only=True. That option was removed when MediaPipe updated its Pose
model. The current application runs the modern full-body Pose model and selects
landmark indices 0-24 so the output has the expected INCLUDE feature slots.

The first 25 indices correspond to the expected upper-body landmark ordering
(nose through hips), while indices 25-32 are lower-body points. This preserves
feature count and index meaning. It does NOT prove that the modern model estimates
the same coordinates as the historical upper_body_only model: the detector/model
was updated, so detector-level and coordinate-level parity must be measured.

Do not describe this adapter as exact or verified model parity until a paired
frame-level comparison using a compatible historical extractor has passed.

Wrist indices for the upstream single-hand slot assignment are 15 (left wrist)
and 16 (right wrist).
"""

from dataclasses import dataclass
import math
import numpy as np

from .preprocess import make_scaled_frame_feature

# Modern BlazePose-33 indices selected to fill the INCLUDE 25-point upper-body slots.
# The index order is compatible; the updated detector is not proven coordinate-identical
# to the legacy upper_body_only=True model used by the upstream generator.
UPPER_BODY_INDICES = list(range(25))

# Wrist landmark indices within the 25-point upper-body pose (same as upstream).
LEFT_WRIST_IDX = 15
RIGHT_WRIST_IDX = 16


@dataclass
class LandmarkFrame:
    pose_x: list[float]
    pose_y: list[float]
    hand1_x: list[float]
    hand1_y: list[float]
    hand2_x: list[float]
    hand2_y: list[float]
    pose_found: bool
    hand_count: int


def _landmark_xy(landmarks) -> tuple[list[float], list[float]]:
    return [lm.x for lm in landmarks.landmark], [lm.y for lm in landmarks.landmark]


def _nan_list(n: int) -> list[float]:
    return [math.nan] * n


def extract_upper_body_pose(pose_results) -> tuple[list[float], list[float], bool]:
    """Select the first 25 upper-body slots from the modern full-body Pose output.

    This preserves the expected INCLUDE feature layout and index ordering. Because
    MediaPipe updated its Pose model when removing ``upper_body_only``, this slice
    is not proof of coordinate-level parity with the historical detector.

    Returns (pose_x, pose_y, pose_found).
    """
    if not pose_results or not pose_results.pose_landmarks:
        return _nan_list(25), _nan_list(25), False

    all_x = [lm.x for lm in pose_results.pose_landmarks.landmark]
    all_y = [lm.y for lm in pose_results.pose_landmarks.landmark]

    if len(all_x) < 25:
        # Unexpected — full-body model should always return 33 landmarks when detected.
        return _nan_list(25), _nan_list(25), False

    # [:25] selects upper-body landmarks (indices 0–24). Lower-body (25–32) are excluded.
    pose_x = all_x[:25]
    pose_y = all_y[:25]
    return pose_x, pose_y, True


def assign_hands(
    hand_landmarks,
    pose_x: list[float],
    pose_y: list[float],
    mode: str = "verified_one_hand_fix",
):
    """Return hand1/hand2 in a reproducible policy.

    verified_one_hand_fix reproduces the notebook's behavior: when only one hand
    is present, use pose wrists (indices 15/16 in the 25-point set) to place it.
    For two hands, retain detector order.
    wrist_nearest orders both hands by the nearest pose wrist.
    """
    hands = hand_landmarks or []
    h1 = _landmark_xy(hands[0]) if len(hands) >= 1 else ([], [])
    h2 = _landmark_xy(hands[1]) if len(hands) >= 2 else ([], [])

    if len(pose_x) >= 17 and len(pose_y) >= 17:
        left = np.array([pose_x[LEFT_WRIST_IDX], pose_y[LEFT_WRIST_IDX]], dtype=np.float32)
        right = np.array([pose_x[RIGHT_WRIST_IDX], pose_y[RIGHT_WRIST_IDX]], dtype=np.float32)

        def d(hand):
            wrist = np.array([hand[0][0], hand[1][0]], dtype=np.float32)
            return float(np.sum((left - wrist) ** 2)), float(np.sum((right - wrist) ** 2))

        if len(hands) == 1 and mode in ("verified_one_hand_fix", "wrist_nearest"):
            dl, dr = d(h1)
            if dr < dl:
                h1, h2 = ([], []), h1
        elif len(hands) == 2 and mode == "wrist_nearest":
            d1l, d1r = d(h1)
            d2l, d2r = d(h2)
            h1_is_left = d1l <= d1r
            h2_is_left = d2l <= d2r
            if not h1_is_left and h2_is_left:
                h1, h2 = h2, h1

    return h1, h2


def landmark_results_to_feature_frame(
    hand_results, pose_results, hand_order_mode: str = "verified_one_hand_fix"
) -> LandmarkFrame:
    """Convert MediaPipe results to the 134-D INCLUDE feature layout.

    The modern full-body Pose result is restricted to its first 25 upper-body slots.
    This is index/order compatible with INCLUDE but not demonstrated to be the same
    detector output as the historical upper_body_only=True model.
    """
    pose_x, pose_y, pose_found = extract_upper_body_pose(pose_results)

    h1, h2 = assign_hands(
        hand_results.multi_hand_landmarks if hand_results else None,
        pose_x if pose_found else [],
        pose_y if pose_found else [],
        mode=hand_order_mode,
    )
    h1x, h1y = h1 if len(h1) == 2 and len(h1[0]) == 21 else (_nan_list(21), _nan_list(21))
    h2x, h2y = h2 if len(h2) == 2 and len(h2[0]) == 21 else (_nan_list(21), _nan_list(21))

    return LandmarkFrame(
        pose_x, pose_y, h1x, h1y, h2x, h2y, pose_found,
        int(len(hand_results.multi_hand_landmarks)) if hand_results and hand_results.multi_hand_landmarks else 0,
    )


def frame_to_normalized_record(frame: LandmarkFrame) -> dict:
    return {
        "pose_x": [frame.pose_x],
        "pose_y": [frame.pose_y],
        "hand1_x": [frame.hand1_x],
        "hand1_y": [frame.hand1_y],
        "hand2_x": [frame.hand2_x],
        "hand2_y": [frame.hand2_y],
    }
