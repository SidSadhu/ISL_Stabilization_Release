from __future__ import annotations

import argparse
import json
import threading
import time

import cv2
import mediapipe as mp

from ..landmarks import landmark_results_to_feature_frame


latest_prediction = {"label": "", "confidence": 0.0, "stable": False}
lock = threading.Lock()


def _prediction_reader():
    while True:
        line = input()
        if not line:
            return
        try:
            msg = json.loads(line)
            if msg.get("type") == "prediction":
                with lock:
                    latest_prediction.update(msg)
        except Exception:
            continue


def main(camera: int = 0, preview: bool = True, hand_order_mode: str = "verified_one_hand_fix") -> None:
    thread = threading.Thread(target=_prediction_reader, daemon=True)
    thread.start()

    hands = mp.solutions.hands.Hands(
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    pose = mp.solutions.pose.Pose(
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open webcam index {camera}")

    frame_id = 0
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                break
            t0 = time.perf_counter()
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            hand_results = hands.process(rgb)
            pose_results = pose.process(rgb)
            frame = landmark_results_to_feature_frame(
                hand_results, pose_results, hand_order_mode=hand_order_mode
            )
            elapsed = (time.perf_counter() - t0) * 1000.0
            payload = {
                "type": "frame",
                "frame_id": frame_id,
                "timestamp": time.time(),
                "mediapipe_ms": elapsed,
                "pose_found": frame.pose_found,
                "hand_count": frame.hand_count,
                "pose_x": frame.pose_x,
                "pose_y": frame.pose_y,
                "hand1_x": frame.hand1_x,
                "hand1_y": frame.hand1_y,
                "hand2_x": frame.hand2_x,
                "hand2_y": frame.hand2_y,
            }
            print(json.dumps(payload, allow_nan=True), flush=True)

            if preview:
                with lock:
                    p = dict(latest_prediction)
                text = f"{p.get('label','')} {float(p.get('confidence',0.0))*100:.1f}%"
                cv2.putText(image, text, (25, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                cv2.imshow("ISL Live Capture", image)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            frame_id += 1
    finally:
        cap.release()
        hands.close()
        pose.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--hand-order-mode", choices=["verified_one_hand_fix", "wrist_nearest"], default="verified_one_hand_fix")
    args = parser.parse_args()
    main(args.camera, preview=not args.no_preview, hand_order_mode=args.hand_order_mode)
