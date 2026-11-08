#!/usr/bin/env python3
"""Evaluate actual browser Canvas JPEG frames against a timestamp-matched raw path.

This is Phase 1 of the consolidated stabilization campaign. It requires the
pinned AI4Bharat checkout, the locked checkpoint, official 77-item validation
manifest/videos, and a complete browser capture bundle created by
serve_browser_validation_capture.py. It does not modify model/source/config and
never calls the app, MQTT, simulator, or firmware.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
SRC = ROOT / "src"
for item in (SRC, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from browser_capture_protocol import CaptureBundleError, parse_capture_payload
from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.feature_extractor import MediaPipeFeatureExtractor
from isl_recognition.labels import invert_label_map, load_label_map
from isl_recognition.iot_protocol import load_label_commands
from isl_recognition.preprocess import preprocess_feature_sequence
from prepare_official_validation_manifest import (
    EXPECTED_VAL_COUNT,
    OFFICIAL_VAL_LIST_SHA256,
    PINNED_REPO_COMMIT,
    load_official_validation_rows,
)
from video_path_utils import resolve_manifest_video

EXPECTED_CHECKPOINT_SHA256 = "8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4"


class EvaluationBlocked(RuntimeError):
    pass


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_assets(repo: Path, checkpoint: Path) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    checkpoint = checkpoint.expanduser().resolve()
    if not repo.is_dir():
        raise EvaluationBlocked(f"AI4Bharat repository is missing: {repo}")
    if not checkpoint.is_file():
        raise EvaluationBlocked(f"Locked checkpoint is missing: {checkpoint}")
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True, timeout=20,
        ).stdout.strip().lower()
    except Exception as exc:
        raise EvaluationBlocked(f"Could not verify AI4Bharat Git HEAD: {type(exc).__name__}: {exc}") from exc
    if commit != PINNED_REPO_COMMIT:
        raise EvaluationBlocked(f"AI4Bharat HEAD is {commit}, expected {PINNED_REPO_COMMIT}")
    transformer_path = repo / "models" / "transformer.py"
    if not transformer_path.is_file():
        raise EvaluationBlocked(f"Pinned model file is missing: {transformer_path}")
    transformer_source = transformer_path.read_text(encoding="utf-8", errors="replace")
    required_dropout_patch = "F.dropout(x, p=0.2, training=self.training)"
    if transformer_source.count(required_dropout_patch) != 1:
        raise EvaluationBlocked(
            "Expected exactly one evaluation-aware dropout call in models/transformer.py. "
            "Do not rerun the completed 20-seed experiment; restore the documented patch and re-audit the diff first."
        )
    # The handover records exactly one intentional upstream change. Verify the
    # worktree diff, not merely the presence of a matching substring.
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", "--unified=0", "--", "models/transformer.py"],
        check=True, capture_output=True, text=True, timeout=20,
    ).stdout
    changed_lines = [line[1:].strip() for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    expected_changed_lines = [
        "x = F.dropout(x, p=0.2)",
        "x = F.dropout(x, p=0.2, training=self.training)",
    ]
    if changed_lines != expected_changed_lines:
        raise EvaluationBlocked(
            "The AI4Bharat transformer.py worktree diff is not the single documented dropout correction. "
            f"Expected {expected_changed_lines!r}, found {changed_lines!r}. Review the upstream diff before evaluation."
        )
    digest = sha256_file(checkpoint)
    if digest != EXPECTED_CHECKPOINT_SHA256:
        raise EvaluationBlocked(
            f"Checkpoint SHA256 mismatch: {digest}; expected locked hash {EXPECTED_CHECKPOINT_SHA256}"
        )
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"], capture_output=True,
        text=True, timeout=20,
    ).stdout.splitlines()
    return {
        "repo_path": str(repo),
        "repo_head": commit,
        "repo_worktree_status": status,
        "dropout_patch_verified": True,
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": digest,
    }


def read_manifest(manifest_path: Path, repo: Path, labels: dict[str, int]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    official_rows, commit, digest = load_official_validation_rows(repo, set(labels))
    if commit != PINNED_REPO_COMMIT or digest != OFFICIAL_VAL_LIST_SHA256 or len(official_rows) != EXPECTED_VAL_COUNT:
        raise EvaluationBlocked("Pinned official validation list failed commit, SHA256 or count verification")
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"video_path", "label"}.issubset(set(reader.fieldnames or [])):
            raise EvaluationBlocked("Manifest CSV requires video_path,label columns")
        actual_rows = list(reader)
    expected_pairs = {(row["video_path"].casefold(), row["label"]) for row in official_rows}
    actual_pairs = {
        (str(row.get("video_path", "")).replace("\\", "/").casefold(), str(row.get("label", "")).strip())
        for row in actual_rows
    }
    if len(actual_rows) != EXPECTED_VAL_COUNT or actual_pairs != expected_pairs:
        raise EvaluationBlocked("CSV manifest does not exactly match the pinned official 77-item validation split")
    for row in actual_rows:
        if row.get("split") and row["split"].strip().lower() != "val":
            raise EvaluationBlocked("Only the official validation split is permitted for this Phase 1 run")
        if row["label"].strip() not in labels:
            raise EvaluationBlocked(f"Validation row contains unknown label: {row['label']}")
    return official_rows, {
        "official_manifest_verified": True,
        "official_manifest_count": len(official_rows),
        "official_repo_commit": commit,
        "official_validation_list_sha256": digest,
        "test_split_used": False,
    }


def read_capture_bundle(capture_dir: Path, official_rows: list[dict[str, str]]) -> list[tuple[dict[str, str], Any]]:
    clips_dir = capture_dir / "clips"
    if not clips_dir.is_dir():
        raise EvaluationBlocked(f"Browser capture bundle directory is missing: {clips_dir}")
    by_path = {row["video_path"].casefold(): row for row in official_rows}
    parsed: dict[str, Any] = {}
    files = sorted(clips_dir.glob("*.islbrw"))
    if len(files) != EXPECTED_VAL_COUNT:
        raise EvaluationBlocked(f"Expected exactly 77 browser capture files; found {len(files)}")
    for file in files:
        payload = file.read_bytes()
        # Read the capture header to obtain the identity, then validate against the official row.
        if len(payload) < 12 or payload[:8] != b"ISLBRW01":
            raise EvaluationBlocked(f"Invalid capture container: {file}")
        import struct
        header_len = struct.unpack_from(">I", payload, 8)[0]
        try:
            header = json.loads(payload[12:12 + header_len].decode("utf-8"))
            video_path = str(header["case"]["video_path"]).replace("\\", "/").casefold()
        except Exception as exc:
            raise EvaluationBlocked(f"Invalid capture identity header in {file}: {exc}") from exc
        expected = by_path.get(video_path)
        if expected is None:
            raise EvaluationBlocked(f"Capture file is not in the official validation manifest: {video_path}")
        if video_path in parsed:
            raise EvaluationBlocked(f"Duplicate browser capture for official path: {video_path}")
        try:
            parsed[video_path] = parse_capture_payload(payload, expected)
        except CaptureBundleError as exc:
            raise EvaluationBlocked(f"Capture failed contract validation ({file.name}): {exc}") from exc
    missing = sorted(set(by_path) - set(parsed))
    if missing:
        raise EvaluationBlocked("Browser capture bundle is incomplete: " + ", ".join(missing[:5]))
    return [(row, parsed[row["video_path"].casefold()]) for row in official_rows]


def extract_browser_frames(extractor: MediaPipeFeatureExtractor, jpeg_frames: tuple[bytes, ...]) -> dict[str, Any]:
    import cv2
    session = extractor.start_session()
    features: list[np.ndarray | None] = []
    pose_count = hand_count = hand1_count = hand2_count = both_hand_count = decoded_count = failures = 0
    latencies: list[float] = []
    started = time.perf_counter()
    try:
        for jpeg in jpeg_frames:
            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                decoded_count += 1
            frame_started = time.perf_counter()
            try:
                item = extractor.extract(jpeg, session)
                vec = np.asarray(item.features, dtype=np.float32)
                if vec.shape != (134,) or np.isinf(vec).any():
                    raise ValueError("Extractor output violates 134-D feature contract")
                features.append(vec.copy())
                pose_count += int(bool(item.pose_found))
                hand_count += int(int(getattr(item, "hand_count", 0)) > 0)
                hand1 = bool(getattr(item, "hand1_found", int(getattr(item, "hand_count", 0)) >= 1))
                hand2 = bool(getattr(item, "hand2_found", int(getattr(item, "hand_count", 0)) >= 2))
                hand1_count += int(hand1)
                hand2_count += int(hand2)
                both_hand_count += int(hand1 and hand2)
            except Exception:
                features.append(None)
                failures += 1
            finally:
                latencies.append((time.perf_counter() - frame_started) * 1000.0)
    finally:
        extractor.close_session(session)
    valid = [item for item in features if item is not None]
    return {
        "features_by_frame": features,
        "feature_sequence": np.asarray(valid, dtype=np.float32) if valid else np.empty((0, 134), dtype=np.float32),
        "submitted_frames": len(jpeg_frames),
        "received_frames": len(jpeg_frames),
        "decoded_frames": decoded_count,
        "extracted_frames": len(valid),
        "extraction_failures": failures,
        "pose_frames": pose_count,
        "hand_frames": hand_count,
        "hand1_frames": hand1_count,
        "hand2_frames": hand2_count,
        "both_hand_frames": both_hand_count,
        "pose_ratio": pose_count / len(valid) if valid else 0.0,
        "hand_ratio": hand_count / len(valid) if valid else 0.0,
        "hand1_ratio": hand1_count / len(valid) if valid else 0.0,
        "hand2_ratio": hand2_count / len(valid) if valid else 0.0,
        "both_hand_ratio": both_hand_count / len(valid) if valid else 0.0,
        "feature_missing_coordinates": int(sum(np.count_nonzero(~np.isfinite(item)) for item in valid)),
        "feature_total_coordinates": int(len(valid) * 134),
        "feature_missing_ratio": (
            float(sum(np.count_nonzero(~np.isfinite(item)) for item in valid) / (len(valid) * 134))
            if valid else 0.0
        ),
        "failure_ratio": failures / max(1, failures + len(valid)),
        "extract_seconds": time.perf_counter() - started,
        "extract_ms_per_frame_median": statistics.median(latencies) if latencies else None,
        "extract_ms_per_frame_p95": float(np.percentile(latencies, 95)) if latencies else None,
    }


def extract_native_frames_at_timestamps(
    extractor: MediaPipeFeatureExtractor,
    video_path: Path,
    timestamps: list[float],
) -> dict[str, Any]:
    """Extract timestamp-matched OpenCV-native frames via lossless PNG encoding."""
    import cv2
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV cannot open source video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    source_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if not np.isfinite(fps) or fps <= 0:
        capture.release()
        raise RuntimeError(f"Source FPS is unavailable or invalid for timestamp alignment: {video_path}")
    session = extractor.start_session()
    features: list[np.ndarray | None] = []
    pose_count = hand_count = hand1_count = hand2_count = both_hand_count = decoded_count = failures = 0
    latencies: list[float] = []
    next_index = 0
    cached_frame: np.ndarray | None = None
    cached_index = -1
    started = time.perf_counter()
    try:
        for timestamp in timestamps:
            target_index = max(0, int(round(timestamp * fps)))
            while next_index <= target_index:
                ok, frame = capture.read()
                if not ok:
                    cached_frame = None
                    cached_index = next_index
                    break
                cached_frame = frame
                cached_index = next_index
                next_index += 1
            if cached_frame is None or cached_index < target_index:
                features.append(None)
                failures += 1
                continue
            ok_encode, encoded = cv2.imencode(".png", cached_frame)
            if not ok_encode:
                features.append(None)
                failures += 1
                continue
            decoded_count += 1
            frame_started = time.perf_counter()
            try:
                item = extractor.extract(encoded.tobytes(), session)
                vec = np.asarray(item.features, dtype=np.float32)
                if vec.shape != (134,) or np.isinf(vec).any():
                    raise ValueError("Extractor output violates 134-D feature contract")
                features.append(vec.copy())
                pose_count += int(bool(item.pose_found))
                hand_count += int(int(getattr(item, "hand_count", 0)) > 0)
                hand1 = bool(getattr(item, "hand1_found", int(getattr(item, "hand_count", 0)) >= 1))
                hand2 = bool(getattr(item, "hand2_found", int(getattr(item, "hand_count", 0)) >= 2))
                hand1_count += int(hand1)
                hand2_count += int(hand2)
                both_hand_count += int(hand1 and hand2)
            except Exception:
                features.append(None)
                failures += 1
            finally:
                latencies.append((time.perf_counter() - frame_started) * 1000.0)
    finally:
        capture.release()
        extractor.close_session(session)
    valid = [item for item in features if item is not None]
    return {
        "features_by_frame": features,
        "feature_sequence": np.asarray(valid, dtype=np.float32) if valid else np.empty((0, 134), dtype=np.float32),
        "source_fps": fps,
        "source_width": source_width,
        "source_height": source_height,
        "source_frame_count": source_frame_count,
        "submitted_frames": len(timestamps),
        "received_frames": len(timestamps),
        "decoded_frames": decoded_count,
        "extracted_frames": len(valid),
        "extraction_failures": failures,
        "pose_frames": pose_count,
        "hand_frames": hand_count,
        "hand1_frames": hand1_count,
        "hand2_frames": hand2_count,
        "both_hand_frames": both_hand_count,
        "pose_ratio": pose_count / len(valid) if valid else 0.0,
        "hand_ratio": hand_count / len(valid) if valid else 0.0,
        "hand1_ratio": hand1_count / len(valid) if valid else 0.0,
        "hand2_ratio": hand2_count / len(valid) if valid else 0.0,
        "both_hand_ratio": both_hand_count / len(valid) if valid else 0.0,
        "feature_missing_coordinates": int(sum(np.count_nonzero(~np.isfinite(item)) for item in valid)),
        "feature_total_coordinates": int(len(valid) * 134),
        "feature_missing_ratio": (
            float(sum(np.count_nonzero(~np.isfinite(item)) for item in valid) / (len(valid) * 134))
            if valid else 0.0
        ),
        "failure_ratio": failures / max(1, failures + len(valid)),
        "extract_seconds": time.perf_counter() - started,
        "extract_ms_per_frame_median": statistics.median(latencies) if latencies else None,
        "extract_ms_per_frame_p95": float(np.percentile(latencies, 95)) if latencies else None,
    }


def verify_diagnostic_iot_lock(settings: Settings, label_map: dict[str, int]) -> dict[str, Any]:
    """Fail closed unless all effective application IoT settings are disabled."""
    if settings.pre_iot_lockdown is not True:
        raise EvaluationBlocked("Application pre-IoT lockdown is not hard-enabled")
    if settings.iot_default_enabled:
        raise EvaluationBlocked("ISL_IOT_ENABLED is true; set it to false before diagnostics")
    mappings = load_label_commands(settings.iot_label_map_path, label_map)
    if mappings:
        raise EvaluationBlocked("IoT label allowlist is not empty; diagnostics require zero mappings")
    return {
        "pre_iot_lockdown": True,
        "iot_default_enabled": False,
        "iot_mapping_count": 0,
        "iot_command_emission": False,
    }


def capture_gate(info: dict[str, Any], settings: Settings, *, diagnostic_mode: bool) -> tuple[bool, bool, str | None]:
    if info["submitted_frames"] < settings.min_capture_frames:
        return False, False, f"submitted frames {info['submitted_frames']} < {settings.min_capture_frames}"
    if info["extracted_frames"] < settings.min_capture_frames:
        return False, False, f"extracted frames {info['extracted_frames']} < {settings.min_capture_frames}"
    if info["pose_ratio"] < settings.min_pose_ratio:
        return False, False, f"pose ratio {info['pose_ratio']:.3f} < {settings.min_pose_ratio:.3f}"
    diagnostic_override = False
    if info["hand_ratio"] < settings.min_hand_ratio:
        if diagnostic_mode and info["hand_ratio"] > 0:
            diagnostic_override = True
        else:
            return False, diagnostic_override, f"hand ratio {info['hand_ratio']:.3f} < {settings.min_hand_ratio:.3f}"
    if info["failure_ratio"] > settings.max_extraction_failure_ratio:
        return False, diagnostic_override, f"failure ratio {info['failure_ratio']:.3f} > {settings.max_extraction_failure_ratio:.3f}"
    return True, diagnostic_override, None


def predict_sequence(model, feature_sequence: np.ndarray, labels_by_index: dict[int, str]) -> dict[str, Any]:
    import torch
    _, padded = preprocess_feature_sequence(feature_sequence, max_frame_len=169, feature_dim=134)
    if padded.shape != (169, 134) or not np.isfinite(padded).all():
        raise RuntimeError("Preprocessed browser/reference input violates (169,134) finite contract")
    started = time.perf_counter()
    model_input = torch.from_numpy(padded).unsqueeze(0)
    logits_tensor = model.predict_logits(model_input)[0]
    probabilities_tensor = torch.softmax(logits_tensor, dim=0)
    top_values, top_indices = torch.topk(probabilities_tensor, k=5)
    latency_ms = (time.perf_counter() - started) * 1000.0
    top_indices_list = [int(value) for value in top_indices.detach().cpu().tolist()]
    probabilities = [float(value) for value in probabilities_tensor.detach().cpu().tolist()]
    return {
        "predicted_label": labels_by_index[top_indices_list[0]],
        "confidence": float(top_values[0].item()),
        "top5_labels": [labels_by_index[index] for index in top_indices_list],
        "top5_probabilities": [float(value) for value in top_values.detach().cpu().tolist()],
        "logits": [float(value) for value in logits_tensor.detach().cpu().tolist()],
        "probabilities": probabilities,
        "top5_indices": top_indices_list,
        "inference_latency_ms": latency_ms,
        "device": str(model.device),
        "padded_sequence": padded,
    }


def paired_feature_differences(raw_info: dict[str, Any], browser_info: dict[str, Any]) -> dict[str, Any]:
    groups = {"pose": (0, 50), "hand1": (50, 92), "hand2": (92, 134)}
    per_group: dict[str, Any] = {}
    paired_values: list[float] = []
    paired_frames = 0
    raw_frames = raw_info["features_by_frame"]
    browser_frames = browser_info["features_by_frame"]
    for raw_vec, browser_vec in zip(raw_frames, browser_frames):
        if raw_vec is None or browser_vec is None:
            continue
        paired_frames += 1
        difference = np.abs(np.asarray(raw_vec, dtype=np.float32) - np.asarray(browser_vec, dtype=np.float32))
        for start, end in groups.values():
            part = difference[start:end]
            finite = part[np.isfinite(part)]
            if finite.size:
                paired_values.extend(float(item) for item in finite)
    for name, (start, end) in groups.items():
        values: list[float] = []
        for raw_vec, browser_vec in zip(raw_frames, browser_frames):
            if raw_vec is None or browser_vec is None:
                continue
            diff = np.abs(np.asarray(raw_vec, dtype=np.float32)[start:end] - np.asarray(browser_vec, dtype=np.float32)[start:end])
            finite = diff[np.isfinite(diff)]
            if finite.size:
                values.extend(float(item) for item in finite)
        per_group[name] = {
            "finite_coordinate_count": len(values),
            "mean_absolute_difference": float(np.mean(values)) if values else None,
            "median_absolute_difference": float(np.median(values)) if values else None,
            "p95_absolute_difference": float(np.percentile(values, 95)) if values else None,
            "max_absolute_difference": float(np.max(values)) if values else None,
        }
    return {
        "paired_successful_frames": paired_frames,
        "mean_absolute_difference_all_finite_coordinates": float(np.mean(paired_values)) if paired_values else None,
        "groups": per_group,
    }


def paired_model_input_differences(raw_prediction: dict[str, Any] | None, browser_prediction: dict[str, Any] | None) -> dict[str, Any]:
    if not raw_prediction or not browser_prediction:
        return {"available": False}
    raw_padded = np.asarray(raw_prediction["padded_sequence"], dtype=np.float32)
    browser_padded = np.asarray(browser_prediction["padded_sequence"], dtype=np.float32)
    if raw_padded.shape != (169, 134) or browser_padded.shape != (169, 134):
        return {"available": False, "reason": "model input shape mismatch"}
    difference = np.abs(raw_padded - browser_padded)
    groups = {"pose": (0, 50), "hand1": (50, 92), "hand2": (92, 134)}
    return {
        "available": True,
        "padded_sequence_mean_absolute_difference": float(np.mean(difference)),
        "padded_sequence_p95_absolute_difference": float(np.percentile(difference, 95)),
        "padded_sequence_max_absolute_difference": float(np.max(difference)),
        "groups": {
            name: {
                "mean_absolute_difference": float(np.mean(difference[:, start:end])),
                "p95_absolute_difference": float(np.percentile(difference[:, start:end], 95)),
            }
            for name, (start, end) in groups.items()
        },
        "logit_mean_absolute_difference": float(np.mean(np.abs(np.asarray(raw_prediction["logits"]) - np.asarray(browser_prediction["logits"])))),
        "logit_max_absolute_difference": float(np.max(np.abs(np.asarray(raw_prediction["logits"]) - np.asarray(browser_prediction["logits"])))),
        "probability_mean_absolute_difference": float(np.mean(np.abs(np.asarray(raw_prediction["probabilities"]) - np.asarray(browser_prediction["probabilities"])))),
        "probability_max_absolute_difference": float(np.max(np.abs(np.asarray(raw_prediction["probabilities"]) - np.asarray(browser_prediction["probabilities"])))),
        "same_top1": raw_prediction["predicted_label"] == browser_prediction["predicted_label"],
        "top5_overlap_count": len(set(raw_prediction["top5_labels"]) & set(browser_prediction["top5_labels"])),
        "confidence_absolute_difference": abs(float(raw_prediction["confidence"]) - float(browser_prediction["confidence"])),
    }


def metrics_for_path(rows: list[dict[str, Any]], path_key: str, ordered_labels: list[str], threshold: float) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    valid = [row for row in rows if row.get(path_key, {}).get("prediction")]
    y_true = [row["label"] for row in valid]
    y_pred = [row[path_key]["prediction"]["predicted_label"] for row in valid]
    y_top5 = [row["label"] in row[path_key]["prediction"]["top5_labels"] for row in valid]
    confidences = [float(row[path_key]["prediction"]["confidence"]) for row in valid]
    accepted = [item for item in valid if float(item[path_key]["prediction"]["confidence"]) >= threshold]
    accepted_true = [item["label"] for item in accepted]
    accepted_pred = [item[path_key]["prediction"]["predicted_label"] for item in accepted]
    thresholds = []
    for candidate in (0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 0.90):
        subset = [item for item in valid if float(item[path_key]["prediction"]["confidence"]) >= candidate]
        thresholds.append({
            "threshold": candidate,
            "accepted_count": len(subset),
            "coverage_over_77": len(subset) / EXPECTED_VAL_COUNT,
            "selective_accuracy": (
                sum(item["label"] == item[path_key]["prediction"]["predicted_label"] for item in subset) / len(subset)
                if subset else None
            ),
        })
    return {
        "expected_validation_count": EXPECTED_VAL_COUNT,
        "predicted_count": len(valid),
        "invalid_or_unpredicted_count": EXPECTED_VAL_COUNT - len(valid),
        "coverage_over_77": len(valid) / EXPECTED_VAL_COUNT,
        "top1_accuracy_on_predicted": float(accuracy_score(y_true, y_pred)) if valid else None,
        "top5_accuracy_on_predicted": float(np.mean(y_top5)) if valid else None,
        "macro_f1_on_predicted": float(f1_score(y_true, y_pred, labels=ordered_labels, average="macro", zero_division=0)) if valid else None,
        "weighted_f1_on_predicted": float(f1_score(y_true, y_pred, labels=ordered_labels, average="weighted", zero_division=0)) if valid else None,
        "classification_report": classification_report(y_true, y_pred, labels=ordered_labels, output_dict=True, zero_division=0) if valid else {},
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=ordered_labels).tolist() if valid else [],
        "text_threshold_uncalibrated": threshold,
        "accepted_count_at_text_threshold": len(accepted),
        "coverage_at_text_threshold_over_77": len(accepted) / EXPECTED_VAL_COUNT,
        "selective_accuracy_at_text_threshold": (
            float(accuracy_score(accepted_true, accepted_pred)) if accepted else None
        ),
        "confidence_min": min(confidences) if confidences else None,
        "confidence_median": float(np.median(confidences)) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
        "risk_coverage_curve": thresholds,
    }


def write_outputs(out_dir: Path, report: dict[str, Any], rows: list[dict[str, Any]], ordered_labels: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    fieldnames = [
        "video_path", "label", "browser_frame_count", "browser_extracted_frames", "browser_pose_ratio",
        "browser_hand_ratio", "browser_failure_ratio", "browser_capture_gate_accepted",
        "browser_diagnostic_override_used", "browser_prediction", "browser_confidence", "browser_top5",
        "raw_frame_count", "raw_extracted_frames", "raw_source_fps", "raw_pose_ratio", "raw_hand_ratio",
        "raw_failure_ratio", "raw_capture_gate_accepted", "raw_prediction", "raw_confidence", "raw_top5",
        "paired_feature_frames", "feature_mad", "pose_feature_mad", "hand1_feature_mad", "hand2_feature_mad",
        "padded_sequence_mad", "logit_mad", "probability_mad", "same_top1_raw_vs_browser", "top5_overlap_count",
    ]
    with (out_dir / "per_clip.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            browser = row.get("browser", {})
            raw = row.get("raw_reference", {})
            browser_pred = browser.get("prediction") or {}
            raw_pred = raw.get("prediction") or {}
            mad = row.get("feature_differences", {})
            groups = mad.get("groups", {})
            model_diff = row.get("model_input_differences", {})
            writer.writerow({
                "video_path": row["video_path"], "label": row["label"],
                "browser_frame_count": browser.get("submitted_frames"),
                "browser_extracted_frames": browser.get("extracted_frames"),
                "browser_pose_ratio": browser.get("pose_ratio"), "browser_hand_ratio": browser.get("hand_ratio"),
                "browser_failure_ratio": browser.get("failure_ratio"),
                "browser_capture_gate_accepted": browser.get("capture_gate_accepted"),
                "browser_diagnostic_override_used": browser.get("diagnostic_override_used"),
                "browser_prediction": browser_pred.get("predicted_label"),
                "browser_confidence": browser_pred.get("confidence"),
                "browser_top5": ";".join(browser_pred.get("top5_labels", [])),
                "raw_frame_count": raw.get("submitted_frames"), "raw_extracted_frames": raw.get("extracted_frames"),
                "raw_source_fps": raw.get("source_fps"), "raw_pose_ratio": raw.get("pose_ratio"),
                "raw_hand_ratio": raw.get("hand_ratio"), "raw_failure_ratio": raw.get("failure_ratio"),
                "raw_capture_gate_accepted": raw.get("capture_gate_accepted"),
                "raw_prediction": raw_pred.get("predicted_label"), "raw_confidence": raw_pred.get("confidence"),
                "raw_top5": ";".join(raw_pred.get("top5_labels", [])),
                "paired_feature_frames": mad.get("paired_successful_frames"),
                "feature_mad": mad.get("mean_absolute_difference_all_finite_coordinates"),
                "pose_feature_mad": groups.get("pose", {}).get("mean_absolute_difference"),
                "hand1_feature_mad": groups.get("hand1", {}).get("mean_absolute_difference"),
                "hand2_feature_mad": groups.get("hand2", {}).get("mean_absolute_difference"),
                "padded_sequence_mad": model_diff.get("padded_sequence_mean_absolute_difference"),
                "logit_mad": model_diff.get("logit_mean_absolute_difference"),
                "probability_mad": model_diff.get("probability_mean_absolute_difference"),
                "same_top1_raw_vs_browser": model_diff.get("same_top1"),
                "top5_overlap_count": model_diff.get("top5_overlap_count"),
            })
    for path_key, filename in (("browser_canvas", "browser_confusion_matrix.csv"), ("opencv_native_reference", "raw_confusion_matrix.csv")):
        matrix = report["recognition_metrics"][path_key].get("confusion_matrix", [])
        if matrix:
            with (out_dir / filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["true\\pred", *ordered_labels])
                for label, values in zip(ordered_labels, matrix):
                    writer.writerow([label, *values])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(os.getenv("AI4BHARAT_REPO", "")) if os.getenv("AI4BHARAT_REPO") else None)
    parser.add_argument("--checkpoint", type=Path, default=Path(os.getenv("ISL_CHECKPOINT", "")) if os.getenv("ISL_CHECKPOINT") else None)
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs" / "browser_canvas_evaluation")
    parser.add_argument("--text-threshold", type=float, default=0.40)
    parser.add_argument("--device", choices=("cpu", "cuda"), default=None)
    args = parser.parse_args()
    try:
        settings = Settings()
        settings.validate()
        diagnostic_safety = verify_diagnostic_iot_lock(settings, load_label_map(args.label_map))
        if abs(settings.confidence_threshold - args.text_threshold) > 1e-9:
            raise EvaluationBlocked(
                f"Configured application text threshold {settings.confidence_threshold:.2f} "
                f"does not match benchmark threshold {args.text_threshold:.2f}. Reconcile .env first; do not silently benchmark a different policy."
            )
        if not 0.0 <= args.text_threshold <= 1.0:
            raise EvaluationBlocked("--text-threshold must be in [0,1]")
        repo = args.repo or settings.ai4bharat_repo
        checkpoint = args.checkpoint or settings.checkpoint
        if repo is None or checkpoint is None:
            raise EvaluationBlocked("Provide --repo/AI4BHARAT_REPO and --checkpoint/ISL_CHECKPOINT")
        asset_report = verify_model_assets(repo, checkpoint)
        labels = load_label_map(args.label_map)
        if len(labels) != 50:
            raise EvaluationBlocked(f"Expected exactly 50 labels, found {len(labels)}")
        ordered_labels = [label for label, _ in sorted(labels.items(), key=lambda item: item[1])]
        labels_by_index = {index: label for label, index in labels.items()}
        official_rows, manifest_report = read_manifest(args.manifest.expanduser().resolve(), repo, labels)
        captures = read_capture_bundle(args.capture_dir.expanduser().resolve(), official_rows)
        data_root = args.data_root.expanduser().resolve()
        missing_videos = [row["video_path"] for row in official_rows if not resolve_manifest_video(data_root, row["video_path"]).is_file()]
        if missing_videos:
            raise EvaluationBlocked(f"{len(missing_videos)} official validation videos are missing from {data_root}; first: {missing_videos[0]}")
        extractor = MediaPipeFeatureExtractor()
        if not extractor.available:
            raise EvaluationBlocked("MediaPipe/OpenCV are unavailable. Install the pinned requirements-mediapipe.txt set.")
        import torch
        torch.set_num_threads(max(1, int(os.getenv("ISL_TORCH_NUM_THREADS", "1"))))
        from isl_recognition.model import INCLUDETransformerSmall
        model = INCLUDETransformerSmall(repo, checkpoint, device=args.device)
        result_rows: list[dict[str, Any]] = []
        started_all = time.perf_counter()
        for index, (row, parsed) in enumerate(captures, start=1):
            print(f"[{index:02d}/77] {row['label']:12s} {row['video_path']}", flush=True)
            case = parsed.header["case"]
            timestamps = [float(frame["video_time_seconds"]) for frame in case["frames"]]
            browser_info = extract_browser_frames(extractor, parsed.frames)
            raw_video = resolve_manifest_video(data_root, row["video_path"])
            raw_info = extract_native_frames_at_timestamps(extractor, raw_video, timestamps)
            browser_gate, browser_override, browser_reason = capture_gate(browser_info, settings, diagnostic_mode=True)
            raw_gate, raw_override, raw_reason = capture_gate(raw_info, settings, diagnostic_mode=True)
            if browser_gate:
                browser_info["prediction"] = predict_sequence(model, browser_info["feature_sequence"], labels_by_index)
            else:
                browser_info["prediction"] = None
            if raw_gate:
                raw_info["prediction"] = predict_sequence(model, raw_info["feature_sequence"], labels_by_index)
            else:
                raw_info["prediction"] = None
            model_input_differences = paired_model_input_differences(
                raw_info.get("prediction"), browser_info.get("prediction")
            )
            browser_info.update({
                "capture_gate_accepted": browser_gate,
                "diagnostic_override_used": browser_override,
                "capture_gate_reason": browser_reason,
                "duplicate_frame_skips": int(case["duplicate_frame_skips"]),
                "timer_ticks": int(case["timer_ticks"]),
                "timer_tick_drops_or_no_new_frame": max(0, int(case["timer_ticks"]) - int(case["frame_count"])),
                "browser_dropped_video_frames": int(case["browser_dropped_video_frames"]),
                "browser_total_video_frames": int(case["browser_total_video_frames"]),
                "container_sha256": parsed.sha256,
            })
            raw_info.update({
                "capture_gate_accepted": raw_gate,
                "diagnostic_override_used": raw_override,
                "capture_gate_reason": raw_reason,
            })
            feature_differences = paired_feature_differences(raw_info, browser_info)
            # Do not serialise frame tensors or full padded arrays into the report.
            for info in (browser_info, raw_info):
                info.pop("features_by_frame", None)
                info.pop("feature_sequence", None)
                if info.get("prediction"):
                    info["prediction"].pop("padded_sequence", None)
            result_rows.append({
                "video_path": row["video_path"], "label": row["label"],
                "browser": browser_info, "raw_reference": raw_info,
                "feature_differences": feature_differences,
                "model_input_differences": model_input_differences,
            })
        elapsed = time.perf_counter() - started_all
        report = {
            "campaign": "ISL Recognition Pipeline Stabilization — Phase 1 paired browser Canvas validation",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "completed" if len(result_rows) == EXPECTED_VAL_COUNT else "incomplete",
            "sample_count": len(result_rows),
            "contract": {
                "class_count": 50, "feature_dimension": 134, "max_sequence_frames": 169,
                "checkpoint_sha256_expected": EXPECTED_CHECKPOINT_SHA256,
                "text_threshold": args.text_threshold, "text_threshold_calibrated": False,
                "production_canvas_width": 640, "production_jpeg_quality": 0.92,
                "capture_timer_interval_ms": 40, "max_capture_seconds": 6.0,
                "max_captured_frames": 169,
                "diagnostic_mode": True, "pre_iot_lockdown": True,
                "iot_enabled": False, "iot_command_emission": False,
            },
            "diagnostic_safety_verification": diagnostic_safety,
            "environment": {
                "python": platform.python_version(), "platform": platform.platform(),
                "torch": torch.__version__, "device": str(model.device),
                "mediapipe_available": extractor.available,
            },
            "assets": asset_report,
            "manifest": manifest_report,
            "methodology": {
                "browser_canvas": "Actual Chrome/Edge HTMLVideoElement -> Canvas 640px width -> toDataURL(image/jpeg,0.92) bytes; no OpenCV resize/re-encode in this path.",
                "opencv_native_reference": "OpenCV-decoded native source video frames matched to browser video.currentTime timestamps using source FPS; encoded to lossless PNG only for the existing MediaPipe JPEG-byte decode API. This is a native-resolution reference path, not a browser decoder parity claim.",
                "frame_alignment": "Frame indices are compared at the same browser-recorded timestamps; OpenCV source frame selection uses round(timestamp*source_fps). Failed extraction slots are excluded from per-coordinate differences; each model sequence uses successful extracted frames in temporal order, matching the app's successful-frame sequence behavior.",
                "limitations": [
                    "The browser capture page reproduces the production Canvas resize/JPEG settings but runs from local validation video files rather than the live webcam.",
                    "The local batch receiver removes WebSocket bufferedAmount/network-pressure drops from this diagnostic; timer duplicate skips and browser decoder dropped-frame counters are still recorded separately.",
                    "The OpenCV native reference uses OpenCV's decoder and FPS-based nearest-frame selection; it is not byte-identical to Chrome's internal decoded source frame.",
                    "The active modern MediaPipe Pose adapter uses full Pose indices 0..24 and is not proven coordinate-equivalent to historical upper_body_only=True.",
                    "This benchmark assesses paired pipeline behavior on the validation split; it does not establish external-signer generalisation or production readiness.",
                ],
            },
            "recognition_metrics": {
                "browser_canvas": metrics_for_path(result_rows, "browser", ordered_labels, args.text_threshold),
                "opencv_native_reference": metrics_for_path(result_rows, "raw_reference", ordered_labels, args.text_threshold),
            },
            "frame_summary": {
                "browser_submitted_frames": sum(row["browser"]["submitted_frames"] for row in result_rows),
                "browser_received_frames": sum(row["browser"]["received_frames"] for row in result_rows),
                "browser_decoded_frames": sum(row["browser"]["decoded_frames"] for row in result_rows),
                "browser_extracted_frames": sum(row["browser"]["extracted_frames"] for row in result_rows),
                "browser_failed_extractions": sum(row["browser"]["extraction_failures"] for row in result_rows),
                "browser_duplicate_timer_skips": sum(row["browser"]["duplicate_frame_skips"] for row in result_rows),
                "browser_decoder_dropped_frames": sum(row["browser"]["browser_dropped_video_frames"] for row in result_rows),
                "browser_diagnostic_gate_accepted_captures": sum(bool(row["browser"]["capture_gate_accepted"]) for row in result_rows),
                "browser_accepted_feature_frames": sum(row["browser"]["extracted_frames"] for row in result_rows if row["browser"]["capture_gate_accepted"]),
                "browser_invalid_or_unaccepted_feature_frames": sum(row["browser"]["extracted_frames"] for row in result_rows if not row["browser"]["capture_gate_accepted"]),
                "raw_reference_submitted_frames": sum(row["raw_reference"]["submitted_frames"] for row in result_rows),
                "raw_reference_decoded_frames": sum(row["raw_reference"]["decoded_frames"] for row in result_rows),
                "raw_reference_extracted_frames": sum(row["raw_reference"]["extracted_frames"] for row in result_rows),
                "raw_reference_failed_extractions": sum(row["raw_reference"]["extraction_failures"] for row in result_rows),
                "raw_reference_accepted_feature_frames": sum(row["raw_reference"]["extracted_frames"] for row in result_rows if row["raw_reference"]["capture_gate_accepted"]),
            },
            "elapsed_seconds": elapsed,
            "per_clip": result_rows,
        }
        out_dir = args.out_dir.expanduser().resolve()
        write_outputs(out_dir, report, result_rows, ordered_labels)
        print(f"\nPhase 1 benchmark completed. Outputs: {out_dir}")
        print(json.dumps({
            "sample_count": report["sample_count"],
            "browser_canvas": report["recognition_metrics"]["browser_canvas"],
            "opencv_native_reference": report["recognition_metrics"]["opencv_native_reference"],
            "frame_summary": report["frame_summary"],
            "elapsed_seconds": elapsed,
        }, indent=2))
        return 0
    except (EvaluationBlocked, CaptureBundleError, ValueError, OSError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
