#!/usr/bin/env python3
"""Run resumable 77-clip feature provenance and preprocessing ablation.

For each official validation clip, compare:
  A. Current application extractor on native OpenCV-decoded frames.
  B. Same application extractor on OpenCV-resized 640px lossless PNG frames.
  C. Same application extractor on OpenCV-resized 640px JPEG quality 0.92 frames.
  D. Pinned upstream generate_keypoints.py, under the explicitly non-historical
     modern-MediaPipe first-25-point compatibility adapter.

This isolates (approximately) resize, JPEG, and current implementation/mapping
contributions. The OpenCV resize is NOT byte-identical to browser Canvas. Actual
browser Canvas results remain those in the Phase 1 metrics report.

All 77 source videos must be local. The runner is sequential, resumable, and
fail-closed on manifest/checkpoint/repository/IoT contract mismatches. It does
not train, tune thresholds, use the test split, or communicate with IoT.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DIAGNOSTIC = SCRIPTS / "diagnose_browser_transport_stages.py"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from isl_recognition.app_config import Settings  # noqa: E402
from isl_recognition.labels import load_label_map  # noqa: E402
from prepare_official_validation_manifest import (  # noqa: E402
    EXPECTED_VAL_COUNT,
    OFFICIAL_VAL_LIST_SHA256,
    PINNED_REPO_COMMIT,
    load_official_validation_rows,
)
from video_path_utils import resolve_manifest_video  # noqa: E402

EXPECTED_CHECKPOINT_SHA256 = "8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4"
VARIANTS = (
    "app_direct_native",
    "resized_640_png",
    "resized_640_jpeg_q92",
    "upstream_original",
)
PAIRS = {
    "native_vs_resized_png": "app_direct_native_vs_resized_png",
    "resize_png_vs_jpeg_q92": "resized_png_vs_jpeg_q92",
    "app_native_vs_upstream_modern_adapter": "app_direct_native_vs_upstream",
    "opencv_q92_vs_upstream_modern_adapter": "opencv_q92_vs_upstream",
}
GROUPS = ("pose", "hand1", "hand2")

# Exact source hashes from the incomplete 76/77 run uploaded on 2026-11-07.
# The only accepted migration is the diagnostic's missing-Pose -> 25 NaN slots
# guard plus this runner's resume-compatibility handling. All other assets,
# source files, model/runtime settings and per-video hashes must match exactly.
PRIOR_76_RUN_DIAGNOSTIC_SHA256 = "3d8df26873a4a7cedf007e382c80717413735db803e8a16ba06f7b8078166118"
PRIOR_76_RUNNER_SHA256 = "b36f0501b75f2d4047b5ae3ab36e65b3a1f94c9e8bdadfde5fe1520869e071c0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_stem(index: int, label: str, path: str) -> str:
    stem = Path(path).stem
    value = f"{index:02d}_{label}_{stem}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def git_model_patch_is_exact(repo: Path) -> bool:
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", "--unified=0", "--", "models/transformer.py"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.splitlines()
    changed = [line[1:].strip() for line in diff
               if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    return changed == [
        "x = F.dropout(x, p=0.2)",
        "x = F.dropout(x, p=0.2, training=self.training)",
    ]


def source_fingerprints(repo: Path) -> dict[str, str]:
    paths = {
        "phase2_diagnostic": DIAGNOSTIC,
        "feature_stage_helpers": SCRIPTS / "diagnose_feature_pipeline_stages.py",
        "phase2_runner": Path(__file__).resolve(),
        "app_config": ROOT / "src" / "isl_recognition" / "app_config.py",
        "app_labels": ROOT / "src" / "isl_recognition" / "labels.py",
        "app_model_adapter": ROOT / "src" / "isl_recognition" / "model.py",
        "app_landmarks": ROOT / "src" / "isl_recognition" / "landmarks.py",
        "app_feature_extractor": ROOT / "src" / "isl_recognition" / "feature_extractor.py",
        "app_preprocess": ROOT / "src" / "isl_recognition" / "preprocess.py",
        "app_frontend": ROOT / "src" / "isl_recognition" / "static" / "app.js",
        "model_requirements": ROOT / "requirements-model.txt",
        "mediapipe_requirements": ROOT / "requirements-mediapipe.txt",
        "iot_config": ROOT / "config" / "iot_label_map.json",
        "upstream_generator": repo / "generate_keypoints.py",
        "upstream_dataset": repo / "dataset.py",
        "upstream_configs": repo / "configs.py",
        "upstream_transformer": repo / "models" / "transformer.py",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"Cannot fingerprint Phase 2 source files: {missing}")
    return {name: sha256_file(path) for name, path in paths.items()}


def validate_preflight(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    checkpoint = args.checkpoint.resolve()
    data_root = args.data_root.resolve()
    manifest = args.manifest.resolve()

    if not DIAGNOSTIC.is_file():
        raise RuntimeError(f"Diagnostic script missing: {DIAGNOSTIC}")
    for path, name in ((repo, "AI4Bharat repository"), (checkpoint, "checkpoint"),
                       (data_root, "video root"), (manifest, "official manifest")):
        if not path.exists():
            raise RuntimeError(f"Required {name} missing: {path}")

    repo_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip().lower()
    if repo_commit != PINNED_REPO_COMMIT:
        raise RuntimeError(f"AI4Bharat commit mismatch: {repo_commit}")
    if not git_model_patch_is_exact(repo):
        raise RuntimeError(
            "AI4Bharat models/transformer.py diff is not exactly the documented "
            "deterministic-inference dropout fix; inspect the worktree before proceeding"
        )
    status_lines = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout.splitlines()
    modified_tracked = sorted(line[3:] for line in status_lines if line and not line.startswith("??"))
    if modified_tracked != ["models/transformer.py"]:
        raise RuntimeError(
            "Unexpected tracked modifications in the pinned upstream checkout: "
            f"{modified_tracked}; expected only models/transformer.py dropout patch"
        )
    unexpected_untracked = sorted(
        line[3:] for line in status_lines if line.startswith("??")
        and line[3:] != "models/transformer.py.pre_isl_deterministic_inference.bak"
    )
    if unexpected_untracked:
        raise RuntimeError(f"Unexpected untracked upstream files: {unexpected_untracked}")

    checkpoint_hash = sha256_file(checkpoint)
    if checkpoint_hash != EXPECTED_CHECKPOINT_SHA256:
        raise RuntimeError(f"Checkpoint SHA256 mismatch: {checkpoint_hash}")

    labels = load_label_map(ROOT / "config" / "label_map_include50.json")
    official_rows, verified_commit, split_hash = load_official_validation_rows(repo, set(labels))
    if verified_commit != PINNED_REPO_COMMIT or split_hash != OFFICIAL_VAL_LIST_SHA256:
        raise RuntimeError("Pinned official validation split verification failed")
    if len(official_rows) != EXPECTED_VAL_COUNT or len(labels) != 50:
        raise RuntimeError("Expected exactly 77 official validation examples and 50 labels")

    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        manifest_rows = list(csv.DictReader(stream))
    official_pairs = {
        (row["video_path"].casefold(), row["label"], row["split"])
        for row in official_rows
    }
    provided_pairs = {
        (row["video_path"].replace("\\", "/").casefold(), row["label"].strip(), row["split"].strip())
        for row in manifest_rows
    }
    if len(manifest_rows) != 77 or provided_pairs != official_pairs:
        raise RuntimeError("CSV manifest is not exactly the pinned official 77-item validation list")
    if any(row["split"] != "val" for row in manifest_rows):
        raise RuntimeError("Non-validation split entry found; evaluation blocked")

    settings = Settings()
    settings.validate()
    if settings.pre_iot_lockdown is not True:
        raise RuntimeError("Hard pre-IoT application lockdown is not enabled")
    if settings.iot_default_enabled:
        raise RuntimeError("ISL_IOT_ENABLED is true; diagnostic blocked")
    iot_map = ROOT / "config" / "iot_label_map.json"
    map_data = json.loads(iot_map.read_text(encoding="utf-8"))
    if map_data.get("mappings") != {}:
        raise RuntimeError("IoT mapping must be empty for Phase 2")

    try:
        import cv2
        import mediapipe as mp
        import torch
    except Exception as exc:
        raise RuntimeError(f"Required Phase 2 runtime import failed: {exc}") from exc

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 2 requires Python 3.12; found {sys.version.split()[0]}")
    if mp.__version__ != "0.10.21":
        raise RuntimeError(f"Phase 2 baseline expects MediaPipe 0.10.21; found {mp.__version__}")
    if cv2.__version__ != "4.11.0":
        raise RuntimeError(f"Phase 2 baseline expects OpenCV 4.11.0; found {cv2.__version__}")
    if not str(torch.__version__).startswith("2.11.0"):
        raise RuntimeError(f"Phase 2 baseline expects PyTorch 2.11.0; found {torch.__version__}")

    manifest_hash = sha256_file(manifest)
    label_map_hash = sha256_file(ROOT / "config" / "label_map_include50.json")
    source_hashes = source_fingerprints(repo)
    runtime = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "opencv": cv2.__version__,
        "mediapipe": mp.__version__,
        "torch": torch.__version__,
        "device_available_cuda": bool(torch.cuda.is_available()),
        "device_selected_by_model": "cuda" if torch.cuda.is_available() else "cpu",
        "iot_enabled": False,
        "iot_mapping_count": 0,
        "pre_iot_lockdown": True,
    }
    fingerprint = {
        "campaign": "phase2_feature_provenance_77_v2",
        "repo_commit": repo_commit,
        "official_split_sha256": split_hash,
        "manifest_sha256": manifest_hash,
        "label_map_sha256": label_map_hash,
        "checkpoint_sha256": checkpoint_hash,
        "source_file_sha256": source_hashes,
        "runtime": runtime,
        "target_width": 640,
        "jpeg_quality": 92,
        "resize": "OpenCV INTER_LINEAR approximation; not exact Canvas",
        "model_contract": {"classes": 50, "features": 134, "frames": 169},
    }
    return {
        "repo": repo,
        "checkpoint": checkpoint,
        "data_root": data_root,
        "manifest": manifest,
        "official_rows": official_rows,
        "labels": labels,
        "repo_commit": repo_commit,
        "split_sha256": split_hash,
        "manifest_sha256": manifest_hash,
        "checkpoint_sha256": checkpoint_hash,
        "fingerprint": fingerprint,
        "runtime": runtime,
    }


def metric(values: list[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "mean": None, "median": None, "p90": None, "max": None}
    clean_sorted = sorted(clean)
    pos = 0.9 * (len(clean_sorted) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    p90 = clean_sorted[lo] + (clean_sorted[hi] - clean_sorted[lo]) * (pos - lo)
    return {
        "count": len(clean),
        "mean": float(mean(clean)),
        "median": float(median(clean)),
        "p90": float(p90),
        "max": float(max(clean)),
    }


def variant_summary(reports: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    valid = [r for r in reports if r.get("status") == "success" and r.get("expected_label")]
    predicted = []
    top5 = []
    confidences = []
    by_class: dict[str, dict[str, int]] = {}
    for report in valid:
        pred = report.get("predictions_same_seed", {}).get(variant, {})
        if not pred:
            continue
        expected = report["expected_label"]
        top1 = pred.get("top1_label")
        top5_labels = [row.get("label") for row in pred.get("top5", [])]
        predicted.append(int(top1 == expected))
        top5.append(int(expected in top5_labels))
        confidences.append(float(pred.get("top1_confidence", 0.0)))
        stats = by_class.setdefault(expected, {"support": 0, "correct": 0})
        stats["support"] += 1
        stats["correct"] += int(top1 == expected)
    total = len(predicted)
    return {
        "predicted_count": total,
        "top1_correct_count": int(sum(predicted)),
        "top1_accuracy": float(sum(predicted) / total) if total else None,
        "top5_correct_count": int(sum(top5)),
        "top5_accuracy": float(sum(top5) / total) if total else None,
        "confidence": metric(confidences),
        "per_class": by_class,
    }


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [r for r in reports if r.get("status") == "success"]
    aggregate_pairs: dict[str, Any] = {}
    pair_agreements: dict[str, Any] = {}
    pair_top1 = {
        "native_vs_resized_png": ("app_direct_native", "resized_640_png"),
        "resize_png_vs_jpeg_q92": ("resized_640_png", "resized_640_jpeg_q92"),
        "app_native_vs_upstream_modern_adapter": ("app_direct_native", "upstream_original"),
        "opencv_q92_vs_upstream_modern_adapter": ("resized_640_jpeg_q92", "upstream_original"),
    }
    for out_name, (left, right) in pair_top1.items():
        pair_agreements[out_name] = sum(
            r.get("predictions_same_seed", {}).get(left, {}).get("top1_label")
            == r.get("predictions_same_seed", {}).get(right, {}).get("top1_label")
            for r in successful
        )

    pair_keys = {
        "native_vs_resized_png": "app_direct_native_vs_resized_png",
        "resize_png_vs_jpeg_q92": "resized_png_vs_jpeg_q92",
        "app_native_vs_upstream_modern_adapter": "app_direct_native_vs_upstream",
        "opencv_q92_vs_upstream_modern_adapter": "opencv_q92_vs_upstream",
    }
    for output_name, report_key in pair_keys.items():
        aggregate_pairs[output_name] = {}
        for group in GROUPS:
            values = [
                r.get("feature_comparisons_after_interpolation", {})
                 .get(report_key, {}).get("groups", {}).get(group, {})
                 .get("mean_absolute_difference")
                for r in successful
            ]
            aggregate_pairs[output_name][group] = metric(
                [v for v in values if v is not None]
            )

    per_clip = []
    for report in reports:
        preds = report.get("predictions_same_seed", {})
        comp = report.get("feature_comparisons_after_interpolation", {})
        row = {
            "video_path": report.get("video_path"),
            "expected_label": report.get("expected_label"),
            "status": report.get("status"),
            "error": report.get("error"),
        }
        for variant in VARIANTS:
            prediction = preds.get(variant, {})
            row[f"{variant}_top1"] = prediction.get("top1_label")
            row[f"{variant}_confidence"] = prediction.get("top1_confidence")
            row[f"{variant}_correct"] = (
                prediction.get("top1_label") == report.get("expected_label")
                if prediction else None
            )
        for output_name, report_key in pair_keys.items():
            row[f"{output_name}_top1_agreement"] = report.get("top1_agreement_by_pair", {}).get(report_key)
            for group in GROUPS:
                row[f"{output_name}_{group}_mad"] = (
                    comp.get(report_key, {}).get("groups", {}).get(group, {})
                    .get("mean_absolute_difference")
                )
        per_clip.append(row)

    return {
        "selected_count": len(reports),
        "successful_count": len(successful),
        "failed_count": len(reports) - len(successful),
        "recognition_by_variant": {
            variant: variant_summary(reports, variant) for variant in VARIANTS
        },
        "top1_agreement_counts": pair_agreements,
        "feature_mad_by_pair_and_group": aggregate_pairs,
        "per_clip": per_clip,
    }


def validate_diagnostic_result(result: dict[str, Any], expected_label: str) -> None:
    if result.get("status") != "success":
        raise RuntimeError("Per-clip diagnostic did not report success")
    if result.get("expected_label") != expected_label:
        raise RuntimeError("Per-clip diagnostic label does not match official manifest")
    if result.get("model_input_shape") != [169, 134]:
        raise RuntimeError(f"Per-clip model input contract mismatch: {result.get('model_input_shape')}")
    frame_counts = result.get("frame_counts_by_path", {})
    if set(frame_counts) != set(VARIANTS) or len(set(frame_counts.values())) != 1:
        raise RuntimeError(f"Per-clip source frame counts differ across variants: {frame_counts}")
    predictions = result.get("predictions_same_seed", {})
    if set(predictions) != set(VARIANTS):
        raise RuntimeError("Per-clip result is missing one or more model variants")
    for variant in VARIANTS:
        prediction = predictions.get(variant) or {}
        if not prediction.get("top1_label") or not isinstance(prediction.get("top1_confidence"), (int, float)):
            raise RuntimeError(f"Invalid top-1 prediction payload for {variant}")
        if not isinstance(prediction.get("top5"), list) or len(prediction["top5"]) != 5:
            raise RuntimeError(f"Expected five top-k labels for {variant}")



def compatible_prior_pose_guard_result(
    cached: dict[str, Any],
    current_fingerprint: dict[str, Any],
    *,
    source_video_sha256: str,
    official_video_path: str,
    expected_label: str,
) -> bool:
    """Allow successful 76-run clips to survive only the known missing-Pose shim.

    The prior run's successful clips necessarily completed without triggering
    the upstream None-pose dereference. The new diagnostic adds a NaN sentinel
    only for missing-pose frames; successful old clip outputs therefore remain
    numerically unchanged. Compatibility is accepted only for the exact two
    known old script hashes and only when every other fingerprint component,
    source video hash, official path and label match.
    """
    provenance = cached.get("phase2_provenance") or {}
    prior = provenance.get("runner_fingerprint")
    if not isinstance(prior, dict):
        return False
    prior_hashes = (prior.get("source_file_sha256") or {})
    if prior_hashes.get("phase2_diagnostic") != PRIOR_76_RUN_DIAGNOSTIC_SHA256:
        return False
    if prior_hashes.get("phase2_runner") != PRIOR_76_RUNNER_SHA256:
        return False

    normalized = json.loads(json.dumps(prior))
    normalized_hashes = normalized.get("source_file_sha256") or {}
    current_hashes = current_fingerprint.get("source_file_sha256") or {}
    if not current_hashes.get("phase2_diagnostic") or not current_hashes.get("phase2_runner"):
        return False
    normalized_hashes["phase2_diagnostic"] = current_hashes["phase2_diagnostic"]
    normalized_hashes["phase2_runner"] = current_hashes["phase2_runner"]
    normalized["source_file_sha256"] = normalized_hashes

    return (
        normalized == current_fingerprint
        and provenance.get("source_video_sha256") == source_video_sha256
        and provenance.get("official_video_path", "").replace("\\", "/").casefold()
            == official_video_path.replace("\\", "/").casefold()
        and provenance.get("expected_label") == expected_label
        and cached.get("status") == "success"
        and cached.get("expected_label") == expected_label
        and cached.get("video_path", "").replace("\\", "/").casefold()
            == official_video_path.replace("\\", "/").casefold()
    )

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "outputs" / "include50_official_val_manifest.csv")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs" / "phase2_feature_provenance_77")
    parser.add_argument("--resume", action="store_true", help="Reuse per-clip results only when their provenance fingerprint matches")
    parser.add_argument(
        "--resume-compatible-missing-pose-guard",
        action="store_true",
        help="Also reuse the exact known successful 76/77 run when only the approved missing-Pose guard/runner hashes differ",
    )
    parser.add_argument("--force", action="store_true", help="Re-run every clip; does not overwrite source videos or captures")
    args = parser.parse_args()

    if args.resume_compatible_missing_pose_guard and not args.resume:
        parser.error("--resume-compatible-missing-pose-guard requires --resume")

    if args.resume and args.force:
        parser.error("--resume and --force cannot be combined")

    try:
        preflight = validate_preflight(args)
        output_dir = args.out_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        reports_dir = output_dir / "per_clip"
        logs_dir = output_dir / "logs"
        reports_dir.mkdir(exist_ok=True)
        logs_dir.mkdir(exist_ok=True)
        existing_reports = list(reports_dir.glob("*.json"))
        if existing_reports and not (args.resume or args.force):
            raise RuntimeError(
                f"{len(existing_reports)} per-clip reports already exist. "
                "Use --resume to reuse fingerprint-matching results or --force to recompute all."
            )

        # Force the subprocess environment to the fail-closed pre-IoT state.
        child_env = os.environ.copy()
        child_env.update({
            "AI4BHARAT_REPO": str(preflight["repo"]),
            "ISL_CHECKPOINT": str(preflight["checkpoint"]),
            "ISL_REFERENCE_VIDEO_DIR": str(preflight["data_root"]),
            "ISL_LABEL_MAP": str(ROOT / "config" / "label_map_include50.json"),
            "ISL_IOT_LABEL_MAP": str(ROOT / "config" / "iot_label_map.json"),
            "ISL_IOT_ENABLED": "false",
            "ISL_CONFIDENCE_THRESHOLD": "0.40",
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + child_env.get("PYTHONPATH", ""),
            "ISL_TORCH_NUM_THREADS": "1",
        })

        run_started = time.perf_counter()
        reports: list[dict[str, Any]] = []
        compatible_reused_count = 0
        rows = preflight["official_rows"]
        for index, row in enumerate(rows, start=1):
            video = resolve_manifest_video(preflight["data_root"], row["video_path"])
            if not video.is_file():
                reports.append({"status": "failed", "video_path": row["video_path"], "expected_label": row["label"], "error": f"Missing source video: {video}"})
                print(f"[{index:02d}/77] BLOCKED missing {row['video_path']}", flush=True)
                continue

            source_hash = sha256_file(video)
            stem = safe_stem(index, row["label"], row["video_path"])
            report_path = reports_dir / f"{stem}.json"
            log_path = logs_dir / f"{stem}.log"
            fingerprint_record = {
                "runner_fingerprint": preflight["fingerprint"],
                "source_video_sha256": source_hash,
                "official_video_path": row["video_path"],
                "expected_label": row["label"],
            }
            expected_fp = hashlib.sha256(
                json.dumps(fingerprint_record, sort_keys=True).encode("utf-8")
            ).hexdigest()

            if args.resume and report_path.is_file():
                try:
                    cached = json.loads(report_path.read_text(encoding="utf-8"))
                    exact_match = (
                        cached.get("phase2_provenance", {}).get("fingerprint") == expected_fp
                        and cached.get("status") == "success"
                    )
                    compatible_match = (
                        args.resume_compatible_missing_pose_guard
                        and compatible_prior_pose_guard_result(
                            cached,
                            preflight["fingerprint"],
                            source_video_sha256=source_hash,
                            official_video_path=row["video_path"],
                            expected_label=row["label"],
                        )
                    )
                    if exact_match or compatible_match:
                        if compatible_match and not exact_match:
                            provenance = cached["phase2_provenance"]
                            provenance["resume_compatibility"] = {
                                "accepted": True,
                                "reason": "Known 76/77 run success reused; only the guarded missing-Pose fallback and runner resume logic changed. This clip's previous run succeeded, so the fallback was not invoked for it.",
                                "prior_diagnostic_sha256": PRIOR_76_RUN_DIAGNOSTIC_SHA256,
                                "prior_runner_sha256": PRIOR_76_RUNNER_SHA256,
                            }
                            provenance["runner_fingerprint"] = preflight["fingerprint"]
                            provenance["fingerprint"] = expected_fp
                            report_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")
                            compatible_reused_count += 1
                        reports.append(cached)
                        reuse_label = "RESUME-COMPAT" if compatible_match and not exact_match else "RESUME"
                        print(f"[{index:02d}/77] {reuse_label} {row['label']:12s} {video.name}", flush=True)
                        continue
                except Exception:
                    pass

            command = [
                sys.executable, str(DIAGNOSTIC), str(video),
                "--expected", row["label"],
                "--resize-width", "640",
                "--output", str(report_path),
            ]
            print(f"[{index:02d}/77] RUN {row['label']:12s} {video.name}", flush=True)
            started = time.perf_counter()
            try:
                proc = subprocess.run(
                    command, cwd=ROOT, env=child_env,
                    capture_output=True, text=True, errors="replace", timeout=300,
                )
                log_path.write_text(
                    "COMMAND: " + subprocess.list2cmdline(command) +
                    "\n\nSTDOUT:\n" + proc.stdout +
                    "\n\nSTDERR:\n" + proc.stderr +
                    f"\nEXIT_CODE: {proc.returncode}\n",
                    encoding="utf-8",
                )
                if proc.returncode != 0 or not report_path.is_file():
                    raise RuntimeError(f"Diagnostic exit {proc.returncode}: {proc.stderr[-2500:]}")
                result = json.loads(report_path.read_text(encoding="utf-8"))
                validate_diagnostic_result(result, row["label"])
                result["video_path"] = row["video_path"]
                result["expected_label"] = row["label"]
                result["phase2_provenance"] = {
                    **fingerprint_record,
                    "fingerprint": expected_fp,
                    "resolved_source_path": str(video),
                    "elapsed_seconds": time.perf_counter() - started,
                }
                report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
                reports.append(result)
                print(f"[{index:02d}/77] PASS {row['label']:12s} {video.name}", flush=True)
            except Exception as exc:
                failure = {
                    "status": "failed", "video_path": row["video_path"],
                    "expected_label": row["label"], "error": f"{type(exc).__name__}: {exc}",
                    "phase2_provenance": fingerprint_record,
                }
                reports.append(failure)
                log_path.write_text(
                    (log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "") +
                    f"\nRUNNER_ERROR: {type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )
                print(f"[{index:02d}/77] FAIL {row['label']:12s} {video.name}: {exc}", flush=True)

        summary = aggregate(reports)
        summary.update({
            "campaign": "ISL Phase 2 — 77-clip feature provenance and preprocessing ablation",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "completed" if summary["successful_count"] == 77 else "incomplete",
            "methodology": {
                "native": "Current application MediaPipe feature mapping on native OpenCV-decoded source frames.",
                "resize_only": "Same application mapping after OpenCV INTER_LINEAR downscale to 640px and lossless PNG transport.",
                "jpeg_q92": "Same application mapping after the same OpenCV resize followed by JPEG quality 0.92 encode/decode; this approximates, but is not pixel-identical to, browser Canvas drawImage/toDataURL.",
                "upstream_adapter": "Pinned AI4Bharat generate_keypoints.py on original source video, shimmed to current MediaPipe full Pose first 25 points. It preserves slot shape/order but is NOT historical upper_body_only detector parity.",
                "all_paths": "Same source video frame order; no resampling or temporal augmentation. Coordinates are scaled to x*1920 and y*1080, interpolated, then padded to 169x134.",
            },
            "limitations": [
                "The actual browser Canvas path was already measured in Phase 1; this Phase 2 offline q92 branch is an OpenCV transform approximation, not a replacement for actual Canvas bytes.",
                "The historical MediaPipe build/model used to generate training keypoints remains unpinned/unproven in the upstream repository requirements; this adapter cannot establish exact legacy detector parity.",
                "This is the 77-item validation split only, with one or two items per class; it is not independent signer generalisation evidence.",
            ],
            "assets": {
                "repo_commit": preflight["repo_commit"],
                "official_split_sha256": preflight["split_sha256"],
                "manifest_sha256": preflight["manifest_sha256"],
                "checkpoint_sha256": preflight["checkpoint_sha256"],
                "fingerprint": preflight["fingerprint"],
            },
            "runtime": preflight["runtime"],
            "elapsed_seconds": time.perf_counter() - run_started,
            "resume_compatibility": {
                "enabled": bool(args.resume_compatible_missing_pose_guard),
                "prior_successes_reused": compatible_reused_count,
                "migration": "Known 76/77 run reused only when all fingerprint components except exact prior diagnostic/runner hashes, source video SHA256, official path and label match.",
            },
            "top1_agreement_counts": summary["top1_agreement_counts"],
            "recognition_by_variant": summary["recognition_by_variant"],
            "feature_mad_by_pair_and_group": summary["feature_mad_by_pair_and_group"],
        })
        summary_path = output_dir / "summary.json"
        rows_out = summary.pop("per_clip")
        fields = sorted({key for row in rows_out for key in row})
        with (output_dir / "per_clip_summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows_out)
        summary["per_clip"] = rows_out
        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print("\n========== PHASE 2 FEATURE PROVENANCE SUMMARY ==========")
        print(json.dumps({k: v for k, v in summary.items() if k != "per_clip"}, indent=2))
        print(f"\nPer-clip reports: {reports_dir}")
        print(f"Summary: {summary_path}")
        print(f"Per-clip CSV: {output_dir / 'per_clip_summary.csv'}")
        return 0 if summary["successful_count"] == 77 else 1

    except Exception as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
