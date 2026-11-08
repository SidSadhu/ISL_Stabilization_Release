from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
from isl_recognition.labels import invert_label_map, load_label_map
from isl_recognition.preprocess import preprocess_feature_sequence
from video_path_utils import resolve_video_path


def video_features(path: Path, extractor: MediaPipeFeatureExtractor) -> tuple[np.ndarray, dict]:
    """Extract at most 169 successful source-frame features, without FPS resampling."""
    import cv2
    if not path.is_file():
        raise FileNotFoundError(str(path))
    session = extractor.start_session()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        extractor.close_session(session)
        raise RuntimeError("OpenCV could not open video")
    features = []
    pose_count = hand_count = failures = 0
    source_index = 0
    extraction_started = time.perf_counter()
    try:
        while len(features) < 169:
            ok, image = cap.read()
            if not ok:
                break
            source_index += 1
            ok_encode, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not ok_encode:
                failures += 1
                continue
            try:
                item = extractor.extract(encoded.tobytes(), session)
                features.append(item.features)
                pose_count += int(item.pose_found)
                hand_count += int(item.hand_count > 0)
            except Exception:
                failures += 1
    finally:
        cap.release()
        extractor.close_session(session)
    return np.asarray(features, dtype=np.float32), {
        "source_frames_seen": source_index,
        "feature_frames": len(features),
        "feature_extraction_failures": failures,
        "pose_ratio": pose_count / len(features) if features else 0.0,
        "hand_ratio": hand_count / len(features) if features else 0.0,
        "feature_extraction_seconds": time.perf_counter() - extraction_started,
    }


def write_report(out_dir: Path, metrics: dict, sample_rows: list[dict], confusion: tuple[list[str], np.ndarray] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if confusion is not None:
        ordered_labels, cm = confusion
        with (out_dir / "confusion_matrix.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["true\\pred", *ordered_labels])
            for label, row_data in zip(ordered_labels, cm):
                writer.writerow([label, *row_data.tolist()])
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if sample_rows:
        keys = sorted({key for row in sample_rows for key in row})
        with (out_dir / "sample_results.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(sample_rows)


def official_manifest_matches(actual_rows: list[dict], expected_rows: list[dict]) -> bool:
    """Compare the complete val manifest against the pinned upstream path/label pairs."""
    actual = {
        (str(row.get("video_path", "")).replace("\\", "/").casefold(), str(row.get("label", "")).strip())
        for row in actual_rows
    }
    expected = {(str(row["video_path"]).casefold(), str(row["label"]).strip()) for row in expected_rows}
    return len(actual_rows) == len(expected_rows) and actual == expected


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and evaluate accessible real videos from a normalized INCLUDE-50 manifest.")
    parser.add_argument("--manifest", required=True, help="CSV with video_path,label and optional split columns")
    parser.add_argument("--data-root", default=".", help="Root used to resolve relative video_path values")
    parser.add_argument("--split", choices=("train", "val", "test"), default="val", help="Evaluation split; defaults to validation. Test requires --allow-test-evaluation.")
    parser.add_argument("--allow-test-evaluation", action="store_true", help="Explicitly unlock the held-out test split for the final frozen evaluation only.")
    parser.add_argument("--out-dir", default="outputs/dataset_evaluation")
    parser.add_argument("--max-samples", type=int, default=0, help="Optional cap for a pilot; 0 means all selected manifest entries")
    parser.add_argument("--min-frames", type=int, default=12)
    args = parser.parse_args()
    if args.split == "test" and not args.allow_test_evaluation:
        print("BLOCKED: test split is locked. Complete model/config/threshold freeze first, then pass --allow-test-evaluation for the final evaluation.", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_file():
        print(f"BLOCKED: Manifest CSV not found: {manifest_path}. Expected columns video_path,label[,split].", file=sys.stderr)
        return 2

    with manifest_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or [])
        if not {"video_path", "label"}.issubset(columns):
            print("Manifest CSV must contain columns: video_path,label and optional split.", file=sys.stderr)
            return 2
        all_entries = list(reader)
    manifest_count = len(all_entries)
    entries = [row for row in all_entries if not row.get("split") or row.get("split", "").lower() == args.split.lower()]
    split_manifest_count = len(entries)
    if args.max_samples > 0:
        entries = entries[:args.max_samples]
    root = Path(args.data_root).expanduser()

    def resolve_video(row: dict) -> Path:
        # Resolve the exact manifest path first; if the same official video ID
        # exists locally with an alternate supported container suffix, use it.
        return resolve_video_path(root, row["video_path"])

    accessible_entries = [row for row in entries if resolve_video(row).is_file()]
    missing_entries = [row for row in entries if not resolve_video(row).is_file()]
    accessible_count = len(accessible_entries)
    missing_count = len(missing_entries)
    sample_rows = [
        {"video_path": row.get("video_path", ""), "label": row.get("label", ""), "status": "missing", "prediction": "", "confidence": "", "error": "file not found"}
        for row in missing_entries
    ]
    expected_split_counts = {"train": 689, "val": 77, "test": 192}
    expected_count = expected_split_counts[args.split]
    base_metrics = {
        "manifest": str(manifest_path.resolve()),
        "data_root": str(root.resolve()),
        "manifest_count": manifest_count,
        "split_manifest_count": split_manifest_count,
        "samples_selected_for_run": len(entries),
        "accessible_count": accessible_count,
        "attempted_count": 0,
        "successful_count": 0,
        "failed_count": 0,
        "missing_count": missing_count,
        "split": args.split,
        "expected_official_split_count": expected_count,
        "official_split_entry_count_match": split_manifest_count == expected_count,
        "official_split_manifest_verified": False,
        "official_split_manifest_reason": "Not yet compared with the pinned upstream split path list.",
        "metrics_status": "not_computed",
        "metric_scope_warning": "Model metrics apply only to successful inferences. Missing files, extraction failures, a partial manifest, or a pilot cap make results incomplete. Confidence sweeps describe selective risk/coverage; they do not automatically select a threshold.",
    }

    def blocked(reason: str, status: str = "blocked_by_external_assets") -> int:
        metrics = {**base_metrics, "metrics_status": status, "blocked_reason": reason}
        for row in accessible_entries:
            sample_rows.append({"video_path": row.get("video_path", ""), "label": row.get("label", ""), "status": "not_attempted", "prediction": "", "confidence": "", "error": reason})
        write_report(out_dir, metrics, sample_rows)
        print("BLOCKED: " + reason, file=sys.stderr)
        print(json.dumps(metrics, indent=2))
        return 2

    settings = Settings()
    label_path = resolve_label_map(settings)
    if not label_path or not label_path.is_file():
        return blocked("Official INCLUDE-50 label map not found. Set ISL_LABEL_MAP or use the bundled official map.")
    try:
        labels = load_label_map(label_path)
        inverse = invert_label_map(labels)
    except Exception as exc:
        return blocked(f"Official label-map validation failed: {type(exc).__name__}: {exc}")

    if args.split == "val":
        try:
            from prepare_official_validation_manifest import load_official_validation_rows
            official_rows, official_commit, official_digest = load_official_validation_rows(
                settings.ai4bharat_repo, set(labels)
            ) if settings.ai4bharat_repo else ([], "", "")
            verified = bool(official_rows) and official_manifest_matches(
                [row for row in all_entries if not row.get("split") or row.get("split", "").lower() == "val"],
                official_rows,
            )
            base_metrics["official_split_manifest_verified"] = verified
            base_metrics["official_split_manifest_reason"] = (
                f"Verified against {official_commit} / SHA256 {official_digest}."
                if verified else "Manifest path/label pairs do not exactly match the pinned 77-item upstream validation split."
            )
        except Exception as exc:
            base_metrics["official_split_manifest_verified"] = False
            base_metrics["official_split_manifest_reason"] = f"Could not verify official validation manifest: {type(exc).__name__}: {exc}"

    if not settings.ai4bharat_repo or not settings.ai4bharat_repo.is_dir() or not settings.checkpoint or not settings.checkpoint.is_file():
        return blocked("Set AI4BHARAT_REPO and ISL_CHECKPOINT to the verified official repository/checkpoint; manifest accessibility counts were written without running inference.")
    extractor = MediaPipeFeatureExtractor()
    if not extractor.available:
        return blocked("Install requirements-mediapipe.txt in the active environment; manifest accessibility counts were written without running inference.")
    try:
        from isl_recognition.model import INCLUDETransformerSmall
        model = INCLUDETransformerSmall(settings.ai4bharat_repo, settings.checkpoint)
    except Exception as exc:
        return blocked(f"Could not initialise official model/dependencies: {type(exc).__name__}: {exc}")

    attempted = successful = failed = 0
    y_true, y_pred = [], []
    y_top5: list[bool] = []
    confidences: list[float] = []
    extraction_latencies: list[float] = []
    inference_latencies: list[float] = []
    end_to_end_latencies: list[float] = []
    for idx, row in enumerate(entries, start=1):
        path = resolve_video(row)
        if not path.is_file():
            continue
        attempted += 1
        sample_started = time.perf_counter()
        try:
            truth = row["label"].strip()
            if truth not in labels:
                raise ValueError(f"Ground-truth label not in official label map: {truth}")
            seq, info = video_features(path, extractor)
            if len(seq) < args.min_frames:
                raise ValueError(f"Only {len(seq)} extracted feature frames; need at least {args.min_frames}; metadata={info}")
            _, padded = preprocess_feature_sequence(seq, 169, 134)
            inference_started = time.perf_counter()
            pred = model.predict(padded)
            inference_seconds = time.perf_counter() - inference_started
            predicted = inverse[int(pred["predicted_index"])]
            confidence = float(pred["confidence"])
            top5_labels = [inverse[int(index)] for index in pred.get("top5_indices", [])]
            is_top5_correct = truth in top5_labels
            extraction_latencies.append(float(info["feature_extraction_seconds"]))
            inference_latencies.append(inference_seconds)
            end_to_end_seconds = time.perf_counter() - sample_started
            end_to_end_latencies.append(end_to_end_seconds)
            successful += 1
            y_true.append(truth)
            y_pred.append(predicted)
            y_top5.append(is_top5_correct)
            confidences.append(confidence)
            sample_rows.append({"video_path": row["video_path"], "label": truth, "status": "success", "prediction": predicted, "confidence": confidence, "top5_predictions": "|".join(top5_labels), "top5_correct": is_top5_correct, "inference_seconds": inference_seconds, "end_to_end_seconds": end_to_end_seconds, **info})
            print(f"[{idx}/{len(entries)}] {truth} -> {predicted} conf={confidence:.4f}")
        except Exception as exc:
            failed += 1
            sample_rows.append({"video_path": row.get("video_path", ""), "label": row.get("label", ""), "status": "failed", "prediction": "", "confidence": "", "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{idx}/{len(entries)}] FAILED {row.get('video_path')}: {type(exc).__name__}: {exc}", file=sys.stderr)

    metrics = {
        **base_metrics,
        "attempted_count": attempted,
        "successful_count": successful,
        "failed_count": failed,
        "missing_count": missing_count,
        "checkpoint": str(settings.checkpoint),
        "label_map": str(label_path),
        "recognition_threshold_setting": settings.confidence_threshold,
        "recognition_threshold_is_calibrated": False,
        "temporal_preprocessing": "Decode source frames in order and use the first 169 successfully extracted feature frames; no FPS resampling. Short sequences are zero-padded by established preprocessing.",
    }
    confusion = None
    if successful:
        from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, precision_score, recall_score
        ordered_labels = [label for label, _ in sorted(labels.items(), key=lambda pair: pair[1])]
        def percentile(values: list[float], q: float) -> float | None:
            return float(np.percentile(values, q)) if values else None

        risk_coverage = []
        for threshold in (0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
            selected = [i for i, confidence in enumerate(confidences) if confidence >= threshold]
            accepted = len(selected)
            correct = sum(y_pred[i] == y_true[i] for i in selected)
            risk_coverage.append({
                "threshold": threshold,
                "accepted_count": accepted,
                "coverage": accepted / successful if successful else 0.0,
                "selective_accuracy": correct / accepted if accepted else None,
                "selective_error_rate": (accepted - correct) / accepted if accepted else None,
            })

        metrics.update({
            "metrics_status": "computed_on_successful_inferences",
            "top1_accuracy": float(accuracy_score(y_true, y_pred)),
            "top5_accuracy": float(np.mean(y_top5)),
            "macro_precision": float(precision_score(y_true, y_pred, labels=ordered_labels, average="macro", zero_division=0)),
            "macro_recall": float(recall_score(y_true, y_pred, labels=ordered_labels, average="macro", zero_division=0)),
            "macro_f1": float(f1_score(y_true, y_pred, labels=ordered_labels, average="macro", zero_division=0)),
            "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
            "classification_report": classification_report(y_true, y_pred, labels=ordered_labels, output_dict=True, zero_division=0),
            "confidence_risk_coverage": risk_coverage,
            "latency_seconds": {
                "feature_extraction_median": percentile(extraction_latencies, 50),
                "feature_extraction_p95": percentile(extraction_latencies, 95),
                "model_inference_median": percentile(inference_latencies, 50),
                "model_inference_p95": percentile(inference_latencies, 95),
                "end_to_end_median": percentile(end_to_end_latencies, 50),
                "end_to_end_p95": percentile(end_to_end_latencies, 95),
            },
            "all_successful_predictions_top5_correct": bool(all(y_top5)),
        })
        confusion = (ordered_labels, confusion_matrix(y_true, y_pred, labels=ordered_labels))
    else:
        metrics["metrics_status"] = "not_computed_no_successful_inferences"
    metrics["evaluation_complete_for_official_split"] = (
        args.max_samples <= 0
        and split_manifest_count == expected_count
        and (args.split != "val" or base_metrics["official_split_manifest_verified"])
        and accessible_count == expected_count
        and attempted == expected_count
        and successful == expected_count
        and failed == 0
    )
    if not metrics["evaluation_complete_for_official_split"]:
        metrics["completeness_warning"] = (
            f"Expected {expected_count} official {args.split} entries; manifest={split_manifest_count}, "
            f"official_manifest_verified={base_metrics.get('official_split_manifest_verified')}, "
            f"accessible={accessible_count}, attempted={attempted}, successful={successful}, failed={failed}. "
            "Do not report this as full-split accuracy."
        )
    write_report(out_dir, metrics, sample_rows, confusion)
    print(json.dumps(metrics, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
