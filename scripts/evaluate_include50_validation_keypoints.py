#!/usr/bin/env python3
"""Evaluate the frozen INCLUDE-50 checkpoint on a pre-generated validation keypoint split.

Input JSON files must be produced by AI4Bharat INCLUDE generate_keypoints.py and contain
label, pose_x/y, hand1_x/y and hand2_x/y. The script does not train or alter the model.
It writes top-1/top-5 metrics, a 50x50 confusion matrix, class metrics, prediction rows,
and a threshold/coverage sweep. It deliberately does not choose or apply a new threshold.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def _canonical_label(value: object) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalpha())


def _load_rows(root: Path, limit: int | None = None) -> list[tuple[Path, dict]]:
    paths = sorted(root.glob("*.json"))
    if limit is not None:
        paths = paths[:limit]
    rows = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Could not read {path}: {type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object in {path}")
        rows.append((path, payload))
    return rows


def _metrics_for_class(confusion: np.ndarray, index: int) -> dict:
    tp = int(confusion[index, index])
    support = int(confusion[index, :].sum())
    predicted = int(confusion[:, index].sum())
    precision = tp / predicted if predicted else 0.0
    recall = tp / support if support else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"support": support, "predicted_count": predicted, "precision": precision, "recall": recall, "f1": f1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keypoints-dir", required=True, type=Path,
                        help="Directory containing only INCLUDE-50 validation keypoint JSON files, e.g. include50_val_keypoints")
    parser.add_argument("--ai4bharat-repo", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--label-map", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/include50_validation_evaluation"))
    parser.add_argument("--limit", type=int, default=None, help="Optional smoke-test limit; do not use a limited run for calibration.")
    args = parser.parse_args()

    root = args.keypoints_dir.expanduser().resolve()
    repo = args.ai4bharat_repo.expanduser().resolve()
    checkpoint = args.checkpoint.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Validation keypoint directory does not exist: {root}")
    if "val" not in root.name.lower():
        parser.error("The keypoint directory name must contain 'val' to reduce accidental train/test evaluation. Use the official validation keypoint folder.")
    if not repo.is_dir() or not checkpoint.is_file():
        parser.error("AI4BHARAT repo and checkpoint must both exist.")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    sys.path.insert(0, str(repo))
    try:
        import torch
        from isl_recognition.labels import load_label_map, invert_label_map
        from isl_recognition.model import INCLUDETransformerSmall
        from isl_recognition.preprocess import preprocess_record
    except Exception as exc:
        parser.error(f"Could not import project/model dependencies: {type(exc).__name__}: {exc}")

    label_path = args.label_map or (project_root / "config" / "label_map_include50.json")
    if not label_path.is_file():
        parser.error(f"Label map not found: {label_path}")
    labels = load_label_map(label_path)
    inverse = invert_label_map(labels)
    rows = _load_rows(root, args.limit)
    if not rows:
        parser.error(f"No *.json keypoint records found in {root}")

    model = INCLUDETransformerSmall(repo, checkpoint)
    torch.manual_seed(20261106)
    confusion = np.zeros((50, 50), dtype=np.int64)
    prediction_rows = []
    class_counts = Counter()
    top1_correct = 0
    top5_correct = 0
    for ordinal, (path, row) in enumerate(rows, start=1):
        true_label = _canonical_label(row.get("label", ""))
        if true_label not in labels:
            raise ValueError(f"Record {path.name} has label {row.get('label')!r}, not in the verified INCLUDE-50 label map")
        _, padded = preprocess_record(row)
        if padded.shape != (169, 134) or not np.isfinite(padded).all():
            raise RuntimeError(f"Preprocessed tensor contract failed for {path.name}: {padded.shape}")
        pred = model.predict(padded)
        top_indices = [int(x) for x in pred["top5_indices"]]
        top_probs = [float(x) for x in pred["top5_probabilities"]]
        if not top_indices or not top_probs or len(top_indices) != len(top_probs):
            raise RuntimeError(f"Model returned invalid top-5 output for {path.name}")
        if any(index not in inverse for index in top_indices):
            raise RuntimeError(f"Model returned an out-of-range class index for {path.name}")
        predicted_label = inverse[top_indices[0]]
        score = float(pred.get("confidence", top_probs[0]))
        if not math.isfinite(score):
            raise RuntimeError(f"Non-finite top-1 score for {path.name}")
        correct = predicted_label == true_label
        top5_hit = labels[true_label] in top_indices[:5]
        class_counts[true_label] += 1
        top1_correct += int(correct)
        top5_correct += int(top5_hit)
        confusion[labels[true_label], top_indices[0]] += 1
        prediction_rows.append({
            "file": path.name,
            "true_label": true_label,
            "predicted_label": predicted_label,
            "top1_score": score,
            "top1_correct": correct,
            "top5_correct": top5_hit,
            "top5": json.dumps([
                {"label": inverse[index], "score": probability}
                for index, probability in zip(top_indices[:5], top_probs[:5])
            ], separators=(",", ":")),
        })
        if ordinal % 25 == 0:
            print(f"Evaluated {ordinal}/{len(rows)} clips")

    count = len(prediction_rows)
    scores = np.asarray([row["top1_score"] for row in prediction_rows], dtype=np.float64)
    correct_mask = np.asarray([row["top1_correct"] for row in prediction_rows], dtype=bool)
    threshold_rows = []
    for threshold in np.round(np.arange(0.10, 0.951, 0.05), 2):
        accepted = scores >= threshold
        accepted_count = int(accepted.sum())
        correct_accepted = int(np.logical_and(accepted, correct_mask).sum())
        threshold_rows.append({
            "threshold": float(threshold),
            "accepted_count": accepted_count,
            "coverage": accepted_count / count,
            "selective_accuracy": correct_accepted / accepted_count if accepted_count else None,
            "false_accept_count": accepted_count - correct_accepted,
        })

    class_metrics = {label: _metrics_for_class(confusion, index) for label, index in labels.items()}
    absent_classes = [label for label, metrics in class_metrics.items() if metrics["support"] == 0]
    macro_f1 = float(np.mean([m["f1"] for m in class_metrics.values() if m["support"] > 0])) if count else 0.0
    pairs = []
    for true_index, true_label in inverse.items():
        for pred_index, predicted_label in inverse.items():
            if true_index != pred_index and confusion[true_index, pred_index] > 0:
                pairs.append({"true_label": true_label, "predicted_label": predicted_label,
                              "count": int(confusion[true_index, pred_index])})
    pairs.sort(key=lambda item: (-item["count"], item["true_label"], item["predicted_label"]))

    output = args.output_dir.expanduser()
    if not output.is_absolute():
        output = project_root / output
    output.mkdir(parents=True, exist_ok=True)
    np.savetxt(output / "confusion_matrix_50x50.csv", confusion, delimiter=",", fmt="%d",
               header=",".join(inverse[index] for index in range(50)), comments="")
    for name, data in (("predictions.csv", prediction_rows), ("threshold_sweep.csv", threshold_rows)):
        with (output / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0].keys()))
            writer.writeheader(); writer.writerows(data)
    class_rows = [{"label": label, **class_metrics[label]} for label in labels]
    with (output / "per_class_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(class_rows[0].keys()))
        writer.writeheader(); writer.writerows(class_rows)
    (output / "top_confusions.json").write_text(json.dumps(pairs[:30], indent=2), encoding="utf-8")

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evaluation_split": "validation",
        "keypoints_directory": str(root),
        "checkpoint": str(checkpoint),
        "ai4bharat_repo": str(repo),
        "label_map": str(label_path),
        "model_weights_modified": False,
        "threshold_policy_changed": False,
        "limited_smoke_test": args.limit is not None,
        "clip_count": count,
        "class_counts": dict(sorted(class_counts.items())),
        "classes_absent_from_this_validation_subset": absent_classes,
        "top1_accuracy": top1_correct / count,
        "top5_accuracy": top5_correct / count,
        "macro_f1_over_present_classes": macro_f1,
        "mean_top1_score": float(np.mean(scores)),
        "threshold_sweep_file": "threshold_sweep.csv",
        "confusion_matrix_file": "confusion_matrix_50x50.csv",
        "per_class_metrics_file": "per_class_metrics.csv",
        "top_confusions_file": "top_confusions.json",
        "predictions_file": "predictions.csv",
        "interpretation": (
            "This is an evaluation of the supplied validation keypoint records only. Do not call a threshold calibrated or production-ready from a limited smoke test. "
            "Choose an operating point only after checking that these records are from the official held-out validation split and reviewing coverage versus selective accuracy."
        ),
    }
    (output / "validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("clip_count", "top1_accuracy", "top5_accuracy", "macro_f1_over_present_classes", "limited_smoke_test")}, indent=2))
    print(f"Saved validation report to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
