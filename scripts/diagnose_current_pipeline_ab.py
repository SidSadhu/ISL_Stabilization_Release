"""Historical Q72 A/B audit of MediaPipe frame paths and Transformer dropout.

DEPRECATED FOR PRODUCTION-PATH VALIDATION: the current RC3 browser path is
640px/JPEG Q92; this script preserves an earlier Q72 experiment.

This script deliberately does NOT claim to reproduce the historical MediaPipe
upper_body_only=True model. It compares:
  A. decoded source frames -> current MediaPipe 0.10.x directly (no JPEG roundtrip)
  B. source frames -> 640px resize + historical JPEG quality 72 -> app extractor

For each resulting tensor it compares current deterministic eval against the
original AI4Bharat final-layer functional-dropout behavior. No source files,
checkpoints, labels, or .env values are modified.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np

from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
from isl_recognition.labels import invert_label_map, load_label_map
from isl_recognition.landmarks import landmark_results_to_feature_frame
from isl_recognition.model import INCLUDETransformerSmall
from isl_recognition.preprocess import make_scaled_frame_feature, preprocess_feature_sequence


def _git_info(repo: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(repo), "commit": None, "status": None}
    try:
        result["commit"] = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        result["status"] = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--short"], text=True, stderr=subprocess.DEVNULL
        ).splitlines()
    except Exception as exc:
        result["git_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _stats(features: np.ndarray) -> dict[str, Any]:
    if not len(features):
        return {"frames": 0, "pose_coverage": 0.0, "any_hand_coverage": 0.0,
                "hand1_coverage": 0.0, "hand2_coverage": 0.0, "both_hands_coverage": 0.0}
    # Feature columns are interleaved XY within each group. A group is considered
    # present for a frame when at least one landmark pair is finite in that group.
    def group_present(start: int, end: int) -> np.ndarray:
        g = features[:, start:end]
        return np.isfinite(g).reshape(len(g), -1, 2).all(axis=2).any(axis=1)
    pose = group_present(0, 50)
    h1 = group_present(50, 92)
    h2 = group_present(92, 134)
    any_hand = h1 | h2
    return {
        "frames": int(len(features)),
        "pose_coverage": float(pose.mean()),
        "any_hand_coverage": float(any_hand.mean()),
        "hand1_coverage": float(h1.mean()),
        "hand2_coverage": float(h2.mean()),
        "both_hands_coverage": float((h1 & h2).mean()),
    }


def _extract_raw_video(path: Path, extractor: MediaPipeFeatureExtractor) -> tuple[np.ndarray, dict[str, Any]]:
    import cv2
    session = extractor.start_session()
    hands_mp, pose_mp = session
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        extractor.close_session(session)
        raise RuntimeError(f"OpenCV could not open video: {path}")
    frames: list[np.ndarray] = []
    pose_frames = hand_frames = 0
    source_frames = 0
    start = time.perf_counter()
    try:
        while len(frames) < 169:
            ok, bgr = cap.read()
            if not ok:
                break
            source_frames += 1
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            hand_results = hands_mp.process(rgb)
            pose_results = pose_mp.process(rgb)
            lm = landmark_results_to_feature_frame(hand_results, pose_results, extractor.hand_order_mode)
            vec = make_scaled_frame_feature(
                lm.pose_x, lm.pose_y, lm.hand1_x, lm.hand1_y, lm.hand2_x, lm.hand2_y,
                allow_nan=True,
            )
            frames.append(vec.astype(np.float32))
            pose_frames += int(lm.pose_found)
            hand_frames += int(lm.hand_count > 0)
    finally:
        cap.release()
        extractor.close_session(session)
    arr = np.asarray(frames, dtype=np.float32)
    elapsed = time.perf_counter() - start
    quality = _stats(arr)
    quality.update({
        "source_frames_seen": source_frames,
        "pose_detected_frames": pose_frames,
        "any_hand_detected_frames": hand_frames,
        "extract_wall_seconds": elapsed,
        "input_path": "decoded source frames, BGR->RGB, no resize/JPEG roundtrip",
    })
    return arr, quality


def _extract_browser_like(path: Path, extractor: MediaPipeFeatureExtractor) -> tuple[np.ndarray, dict[str, Any]]:
    import cv2
    session = extractor.start_session()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        extractor.close_session(session)
        raise RuntimeError(f"OpenCV could not open video: {path}")
    frames: list[np.ndarray] = []
    source_frames = 0
    failures = 0
    start = time.perf_counter()
    try:
        while len(frames) < 169:
            ok, bgr = cap.read()
            if not ok:
                break
            source_frames += 1
            h, w = bgr.shape[:2]
            if w != 640:
                target_h = max(1, round(h * 640 / w))
                bgr = cv2.resize(bgr, (640, target_h), interpolation=cv2.INTER_AREA)
            ok_encode, encoded = cv2.imencode(
                ".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 72]
            )
            if not ok_encode:
                failures += 1
                continue
            item = extractor.extract(encoded.tobytes(), session)
            frames.append(item.features.astype(np.float32))
    finally:
        cap.release()
        extractor.close_session(session)
    arr = np.asarray(frames, dtype=np.float32)
    elapsed = time.perf_counter() - start
    quality = _stats(arr)
    quality.update({
        "source_frames_seen": source_frames,
        "extraction_failures": failures,
        "extract_wall_seconds": elapsed,
        "input_path": "historical 640px resize + JPEG quality 72 + app MediaPipeFeatureExtractor",
        "note": "OpenCV resize approximates browser canvas resize; it is not pixel-identical to the browser.",
    })
    return arr, quality


def _top5(model: INCLUDETransformerSmall, padded: np.ndarray, inverse: dict[int, str]) -> dict[str, Any]:
    prediction = model.predict(padded)
    return {
        "top1": inverse[int(prediction["predicted_index"])],
        "top1_score": float(prediction["confidence"]),
        "top5": [
            {"rank": i + 1, "label": inverse[int(idx)], "score": float(score)}
            for i, (idx, score) in enumerate(zip(prediction["top5_indices"], prediction["top5_probabilities"]))
        ],
    }


def _upstream_dropout_repeats(model: INCLUDETransformerSmall, padded: np.ndarray,
                              inverse: dict[int, str], repeats: int, seed: int) -> list[dict[str, Any]]:
    """Re-enable only the upstream Transformer's unconditional final F.dropout.

    AI4Bharat's source at the pinned commit calls F.dropout(x, p=0.2) without
    passing self.training. This proxy affects that module-level call only; it
    does not turn on dropout modules inside BertLayer or PositionEmbedding.
    """
    import torch
    transformer_module = importlib.import_module("models.transformer")
    original_F = transformer_module.F
    functional = torch.nn.functional

    class _UpstreamFunctionalProxy:
        @staticmethod
        def dropout(input, p=0.5, training=True, inplace=False):
            return functional.dropout(input, p=p, training=True, inplace=inplace)

    try:
        transformer_module.F = _UpstreamFunctionalProxy
        rows = []
        for i in range(repeats):
            torch.manual_seed(seed + i)
            rows.append(_top5(model, padded, inverse))
        return rows
    finally:
        transformer_module.F = original_F


def _summarize_dropout(rows: list[dict[str, Any]], expected: str) -> dict[str, Any]:
    top1_counts: dict[str, int] = {}
    top5_hits = 0
    scores = []
    for row in rows:
        label = row["top1"]
        top1_counts[label] = top1_counts.get(label, 0) + 1
        top5_hits += int(any(x["label"] == expected for x in row["top5"]))
        scores.append(row["top1_score"])
    return {
        "repeats": len(rows),
        "top1_counts": top1_counts,
        "expected_top1_count": top1_counts.get(expected, 0),
        "expected_top5_count": top5_hits,
        "mean_top1_score": float(np.mean(scores)) if scores else None,
        "min_top1_score": float(np.min(scores)) if scores else None,
        "max_top1_score": float(np.max(scores)) if scores else None,
        "examples": rows,
    }


def _resolve_entries(args) -> list[dict[str, str]]:
    with args.manifest.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        if not {"video_path", "label"}.issubset(fields):
            raise ValueError("Manifest must contain video_path and label columns")
        rows = [r for r in reader if (not r.get("split") or r.get("split", "").lower() == args.split.lower())]
    if args.max_samples > 0:
        rows = rows[:args.max_samples]
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only current-pipeline A/B and dropout-semantics audit")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--split", default="val", choices=["train", "val", "test"])
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--dropout-repeats", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--output", type=Path, default=Path("outputs/diagnostics/current_pipeline_ab.json"))
    args = parser.parse_args()

    settings = Settings()
    label_path = resolve_label_map(settings)
    if not settings.ai4bharat_repo or not settings.checkpoint or not label_path:
        raise RuntimeError("Set AI4BHARAT_REPO, ISL_CHECKPOINT and ISL_LABEL_MAP before running")
    labels = load_label_map(label_path)
    inverse = invert_label_map(labels)
    extractor = MediaPipeFeatureExtractor()
    if not extractor.available:
        raise RuntimeError("OpenCV/MediaPipe are not available in the active environment")
    model = INCLUDETransformerSmall(settings.ai4bharat_repo, settings.checkpoint)

    entries = _resolve_entries(args)
    root = args.data_root
    report: dict[str, Any] = {
        "report_type": "current-version diagnostic only; NOT historical MediaPipe parity or final accuracy report",
        "runtime": {},
        "upstream_repo": _git_info(Path(settings.ai4bharat_repo)),
        "checkpoint": str(settings.checkpoint),
        "label_map": str(label_path),
        "split": args.split,
        "manifest": str(args.manifest),
        "data_root": str(root),
        "selected_entries": len(entries),
        "pose_legacy_equivalence": "NOT_TESTED: current MediaPipe does not expose upper_body_only; this script tests current Pose only",
        "items": [],
    }
    import cv2
    import mediapipe as mp
    import torch
    report["runtime"] = {
        "python": sys.version,
        "mediapipe": mp.__version__,
        "opencv": cv2.__version__,
        "torch": torch.__version__,
        "pose_signature": str(__import__("inspect").signature(mp.solutions.pose.Pose)),
        "supports_upper_body_only": "upper_body_only" in __import__("inspect").signature(mp.solutions.pose.Pose).parameters,
    }

    for idx, row in enumerate(entries, start=1):
        raw_path = Path(row["video_path"])
        video = raw_path if raw_path.is_absolute() else root / raw_path
        truth = row["label"].strip()
        item: dict[str, Any] = {"index": idx, "video_path": str(video), "expected": truth, "split": row.get("split", args.split)}
        if truth not in labels:
            item["error"] = f"Label {truth!r} is absent from the configured model label map"
            report["items"].append(item)
            continue
        try:
            raw_seq, raw_quality = _extract_raw_video(video, extractor)
            browser_seq, browser_quality = _extract_browser_like(video, extractor)
            if len(raw_seq) < 12 or len(browser_seq) < 12:
                raise RuntimeError(f"Too few frames: raw={len(raw_seq)}, browser_like={len(browser_seq)}")
            raw_unpadded, raw_padded = preprocess_feature_sequence(raw_seq, 169, 134)
            browser_unpadded, browser_padded = preprocess_feature_sequence(browser_seq, 169, 134)
            common = min(len(raw_unpadded), len(browser_unpadded))
            diff = np.abs(raw_unpadded[:common] - browser_unpadded[:common])
            group_diffs = {
                "pose_0_50": float(diff[:, :50].mean()),
                "hand1_50_92": float(diff[:, 50:92].mean()),
                "hand2_92_134": float(diff[:, 92:134].mean()),
            }
            raw_det = _top5(model, raw_padded, inverse)
            browser_det = _top5(model, browser_padded, inverse)
            raw_dropout = _upstream_dropout_repeats(model, raw_padded, inverse, args.dropout_repeats, args.seed)
            browser_dropout = _upstream_dropout_repeats(model, browser_padded, inverse, args.dropout_repeats, args.seed)
            item.update({
                "raw_source": {"quality": raw_quality, "deterministic_eval": raw_det,
                               "upstream_dropout": _summarize_dropout(raw_dropout, truth)},
                "browser_like": {"quality": browser_quality, "deterministic_eval": browser_det,
                                 "upstream_dropout": _summarize_dropout(browser_dropout, truth)},
                "feature_difference": {
                    "compared_frames": common,
                    "mean_absolute_all": float(diff.mean()),
                    "p95_absolute_all": float(np.percentile(diff, 95)),
                    "mean_absolute_by_group": group_diffs,
                },
                "correct_top1_raw_deterministic": raw_det["top1"] == truth,
                "correct_top5_raw_deterministic": any(x["label"] == truth for x in raw_det["top5"]),
                "correct_top1_browser_deterministic": browser_det["top1"] == truth,
                "correct_top5_browser_deterministic": any(x["label"] == truth for x in browser_det["top5"]),
            })
            print(f"[{idx}/{len(entries)}] {truth}: raw={raw_det['top1']} browser={browser_det['top1']} "
                  f"raw-hand={raw_quality['any_hand_coverage']:.1%} browser-hand={browser_quality['any_hand_coverage']:.1%}")
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[{idx}/{len(entries)}] FAILED {video}: {item['error']}", file=sys.stderr)
        report["items"].append(item)

    valid = [x for x in report["items"] if "error" not in x]
    report["summary"] = {
        "successful_videos": len(valid),
        "failed_videos": len(report["items"]) - len(valid),
        "raw_deterministic_top1": sum(x["correct_top1_raw_deterministic"] for x in valid),
        "raw_deterministic_top5": sum(x["correct_top5_raw_deterministic"] for x in valid),
        "browser_deterministic_top1": sum(x["correct_top1_browser_deterministic"] for x in valid),
        "browser_deterministic_top5": sum(x["correct_top5_browser_deterministic"] for x in valid),
        "warning": "Small selected split only. This is not a final accuracy claim and does not test the legacy upper_body_only Pose model.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nSummary:")
    print(json.dumps(report["summary"], indent=2))
    print(f"\nFull report: {args.output.resolve()}")
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
