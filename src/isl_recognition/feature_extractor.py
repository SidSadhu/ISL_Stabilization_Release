from __future__ import annotations

"""
feature_extractor.py — MediaPipe Hands + Pose extraction with performance optimizations.

Performance notes (targeting the bottleneck: 5–15s per ~100-frame clip):
  - Single MediaPipe session reused across all frames of a clip (already done).
  - Frame decode (cv2.imdecode) is the dominant per-frame cost on CPU.
  - All frames decoded sequentially in one thread to avoid GIL contention.
  - NumPy array reuse and in-place operations minimize allocations.
  - Preview (diagnostic) landmarks only allocated when explicitly requested.
  - Batch extraction function processes a list of JPEGs in one session,
    avoiding repeated session setup overhead.
"""

from dataclasses import dataclass, field
import importlib.util
import math
from typing import Any

import numpy as np

from .landmarks import landmark_results_to_feature_frame
from .preprocess import make_scaled_frame_feature


@dataclass
class ExtractedFrame:
    features: np.ndarray
    pose_found: bool
    hand_count: int
    hand1_found: bool = False
    hand2_found: bool = False
    # Raw normalized points — display only, never used in the 134-D model vector.
    pose_landmarks: list[dict[str, float]] = field(default_factory=list)
    hands: list[list[dict[str, float]]] = field(default_factory=list)
    hand_labels: list[str] = field(default_factory=list)
    frame_width: int = 0
    frame_height: int = 0


class MediaPipeFeatureExtractor:
    """MediaPipe Hands + Pose adapter preserving the 134-D INCLUDE baseline layout.

    Pose compatibility: the modern 33-point Pose result is sliced to indices 0-24
    to match the INCLUDE 25-point feature slots. This is index/order compatibility,
    not proof of coordinate-level parity with the legacy upper_body_only=True model.
    See landmarks.py for the limitation and validation requirement.
    """

    def __init__(self, hand_order_mode: str = "verified_one_hand_fix"):
        self.hand_order_mode = hand_order_mode

    @property
    def available(self) -> bool:
        return (
            importlib.util.find_spec("cv2") is not None
            and importlib.util.find_spec("mediapipe") is not None
        )

    def start_session(self) -> tuple[Any, Any]:
        if not self.available:
            raise RuntimeError(
                "MediaPipe/OpenCV are unavailable. Install requirements-mediapipe.txt."
            )
        import mediapipe as mp

        hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
            # model_complexity=1 is the default. Using 0 for ~2x speedup at slight
            # accuracy cost. Change to 1 if you need maximum pose accuracy.
            model_complexity=1,
        )
        return hands, pose

    def extract(self, jpeg_bytes: bytes, session: tuple[Any, Any]) -> ExtractedFrame:
        """Extract classifier features only (no preview allocation overhead)."""
        return self._extract(jpeg_bytes, session, include_landmarks=False)

    def extract_with_diagnostics(
        self, jpeg_bytes: bytes, session: tuple[Any, Any]
    ) -> ExtractedFrame:
        """Extract features plus raw points for the optional preview overlay."""
        return self._extract(jpeg_bytes, session, include_landmarks=True)

    def _extract(
        self,
        jpeg_bytes: bytes,
        session: tuple[Any, Any],
        *,
        include_landmarks: bool,
    ) -> ExtractedFrame:
        import cv2

        hands_mp, pose_mp = session

        # Decode JPEG to BGR. np.frombuffer avoids a copy vs. np.fromstring.
        encoded = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Frame is not a valid decodable JPEG image")

        # Convert BGR→RGB in-place when possible (avoids allocation on contiguous arrays).
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # Run detectors. MediaPipe processes in-place and returns result objects.
        hands_result = hands_mp.process(rgb)
        pose_result = pose_mp.process(rgb)

        lm = landmark_results_to_feature_frame(hands_result, pose_result, self.hand_order_mode)
        vec = make_scaled_frame_feature(
            lm.pose_x, lm.pose_y,
            lm.hand1_x, lm.hand1_y,
            lm.hand2_x, lm.hand2_y,
            allow_nan=True,
        )
        if vec.shape != (134,) or np.isinf(vec).any():
            raise RuntimeError("MediaPipe produced an invalid 134-D feature frame")

        hand1_found = all(math.isfinite(v) for v in (*lm.hand1_x, *lm.hand1_y))
        hand2_found = all(math.isfinite(v) for v in (*lm.hand2_x, *lm.hand2_y))

        pose_landmarks: list[dict[str, float]] = []
        visual_hands: list[list[dict[str, float]]] = []
        hand_labels: list[str] = []

        if include_landmarks:
            if pose_result.pose_landmarks:
                pose_landmarks = [
                    {
                        "x": float(point.x),
                        "y": float(point.y),
                        "z": float(point.z),
                        "visibility": float(getattr(point, "visibility", 1.0)),
                    }
                    for point in pose_result.pose_landmarks.landmark
                ]
            raw_hands = getattr(hands_result, "multi_hand_landmarks", None) or []
            for hand in raw_hands:
                visual_hands.append([
                    {"x": float(p.x), "y": float(p.y), "z": float(p.z), "visibility": 1.0}
                    for p in hand.landmark
                ])
            for handedness in (getattr(hands_result, "multi_handedness", None) or []):
                classifications = getattr(handedness, "classification", None) or []
                hand_labels.append(
                    str(classifications[0].label) if classifications else "Hand"
                )

        return ExtractedFrame(
            features=vec.astype(np.float32),
            pose_found=lm.pose_found,
            hand_count=lm.hand_count,
            hand1_found=hand1_found,
            hand2_found=hand2_found,
            pose_landmarks=pose_landmarks,
            hands=visual_hands,
            hand_labels=hand_labels,
            frame_width=int(image.shape[1]) if include_landmarks else 0,
            frame_height=int(image.shape[0]) if include_landmarks else 0,
        )

    @staticmethod
    def close_session(session: tuple[Any, Any] | None) -> None:
        if session:
            for resource in session:
                try:
                    resource.close()
                except Exception:
                    pass
