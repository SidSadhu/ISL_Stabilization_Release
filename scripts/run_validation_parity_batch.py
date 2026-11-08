#!/usr/bin/env python
"""Run paired app/reference feature diagnostics on frozen official validation clips.

The script reads the completed validation's metrics.json and sample_results.csv,
selects every top-1 error plus a small set of correctly classified controls, and
runs diagnose_reference_parity.py for each selected clip. It never accesses the
test split or changes model/data files.

IMPORTANT: the reference path uses the pinned upstream generate_keypoints.py
under the installed MediaPipe version. On modern MediaPipe, upper_body_only is
not available; the parity script applies the documented first-25-points
compatibility adapter. This does NOT establish parity with the historical
legacy upper_body_only detector.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PARITY_SCRIPT = SCRIPTS / "diagnose_reference_parity.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from video_path_utils import resolve_video_path  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def select_cases(rows: list[dict[str, str]], max_errors: int = 0, controls: int = 10) -> list[dict[str, Any]]:
    successful = [r for r in rows if r.get("status") == "success" and r.get("label") and r.get("prediction")]
    errors = [r for r in successful if r["label"].strip() != r["prediction"].strip()]
    correct = [r for r in successful if r["label"].strip() == r["prediction"].strip()]
    errors.sort(key=lambda r: (float(r.get("confidence") or 0.0), r.get("label", "")))
    if max_errors > 0:
        errors = errors[:max_errors]

    # Prefer one correctly classified control from a class that also has an error.
    # This helps distinguish class-specific failures from a generally degraded path.
    erroneous_labels = {r["label"].strip() for r in errors}
    controls_pool = [r for r in correct if r["label"].strip() in erroneous_labels]
    controls_pool.sort(key=lambda r: (-float(r.get("confidence") or 0.0), r.get("label", "")))

    selected_controls: list[dict[str, str]] = []
    seen_labels: set[str] = set()
    for row in controls_pool:
        label = row["label"].strip()
        if label not in seen_labels:
            selected_controls.append(row)
            seen_labels.add(label)
        if len(selected_controls) >= controls:
            break

    # If same-class controls are insufficient, add high-confidence correct examples
    # from other classes to provide global sanity controls.
    if len(selected_controls) < controls:
        existing_paths = {r.get("video_path", "") for r in selected_controls}
        for row in sorted(correct, key=lambda r: (-float(r.get("confidence") or 0.0), r.get("label", ""))):
            if row.get("video_path", "") not in existing_paths:
                selected_controls.append(row)
                existing_paths.add(row.get("video_path", ""))
            if len(selected_controls) >= controls:
                break

    cases: list[dict[str, Any]] = []
    for kind, group in (("error", errors), ("control", selected_controls)):
        for row in group:
            cases.append({
                "case_type": kind,
                "label": row["label"].strip(),
                "baseline_prediction": row["prediction"].strip(),
                "baseline_confidence": float(row.get("confidence") or 0.0),
                "baseline_top5": row.get("top5_predictions", ""),
                "video_path": row["video_path"],
            })
    return cases


def summarize_reports(cases: list[dict[str, Any]], reports: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [(c, r) for c, r in zip(cases, reports) if r.get("status") == "success"]
    def values_for(key: str) -> list[float]:
        vals = []
        for _, report in valid:
            value = report
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if isinstance(value, (int, float)):
                vals.append(float(value))
        return vals
    diffs = values_for("feature_parity.mean_absolute_feature_difference")
    p95s = values_for("feature_parity.p95_absolute_feature_difference")
    same_top1 = 0
    app_correct = 0
    reference_correct = 0
    app_top5_correct = 0
    reference_top5_correct = 0
    exact_features = 0
    for case, report in valid:
        app_result = report.get("app_pipeline_prediction_same_rng_seed", {})
        ref_result = report.get("official_pipeline_prediction_same_rng_seed", {})
        app_pred = app_result.get("top1_label")
        ref_pred = ref_result.get("top1_label")
        same_top1 += int(bool(app_pred) and app_pred == ref_pred)
        app_correct += int(app_pred == case["label"])
        reference_correct += int(ref_pred == case["label"])
        app_top5_correct += int(any(x.get("label") == case["label"] for x in app_result.get("top5", [])))
        reference_top5_correct += int(any(x.get("label") == case["label"] for x in ref_result.get("top5", [])))
        exact_features += int(bool(report.get("feature_parity", {}).get("exactly_equal")))
    import numpy as np
    return {
        "diagnostic_type": "paired_app_evaluator_vs_upstream_generator_current_mediapipe",
        "warning": "The upstream generator is run with the installed modern MediaPipe through a first-25-landmark compatibility adapter. This is not historical upper_body_only detector parity.",
        "selected_count": len(cases),
        "successful_diagnostic_count": len(valid),
        "failed_diagnostic_count": len(cases) - len(valid),
        "error_case_count": sum(c["case_type"] == "error" for c in cases),
        "control_case_count": sum(c["case_type"] == "control" for c in cases),
        "median_mean_absolute_feature_difference": float(np.median(diffs)) if diffs else None,
        "p95_mean_absolute_feature_difference": float(np.percentile(diffs, 95)) if diffs else None,
        "median_p95_absolute_feature_difference": float(np.median(p95s)) if p95s else None,
        "exact_feature_match_count": exact_features,
        "same_top1_between_paths": same_top1,
        "app_path_top1_correct_count": app_correct,
        "reference_path_top1_correct_count": reference_correct,
        "app_path_top5_correct_count": app_top5_correct,
        "reference_path_top5_correct_count": reference_top5_correct,
        "cases": [
            {
                "case_type": case["case_type"],
                "label": case["label"],
                "baseline_prediction": case["baseline_prediction"],
                "baseline_confidence": case["baseline_confidence"],
                "video_path": case["video_path"],
                "diagnostic_status": report.get("status"),
                "app_top1": report.get("app_pipeline_prediction_same_rng_seed", {}).get("top1_label"),
                "reference_top1": report.get("official_pipeline_prediction_same_rng_seed", {}).get("top1_label"),
                "app_top5_correct": any(x.get("label") == case["label"] for x in report.get("app_pipeline_prediction_same_rng_seed", {}).get("top5", [])),
                "reference_top5_correct": any(x.get("label") == case["label"] for x in report.get("official_pipeline_prediction_same_rng_seed", {}).get("top5", [])),
                "mean_absolute_feature_difference": report.get("feature_parity", {}).get("mean_absolute_feature_difference"),
                "p95_absolute_feature_difference": report.get("feature_parity", {}).get("p95_absolute_feature_difference"),
                "app_frames": report.get("app_frames_processed"),
                "reference_frames": report.get("official_frames_processed"),
                "pose_compatibility_mode": report.get("official_generator", {}).get("pose_compatibility_mode"),
            }
            for case, report in zip(cases, reports)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("outputs/include50_validation_baseline/sample_results.csv"))
    parser.add_argument("--metrics", type=Path, default=None, help="Defaults to metrics.json beside --results")
    parser.add_argument("--data-root", type=Path, default=None, help="Defaults to ISL_REFERENCE_VIDEO_DIR from project Settings")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/validation_parity_batch"))
    parser.add_argument("--max-errors", type=int, default=0, help="0 means all top-1 errors")
    parser.add_argument("--controls", type=int, default=10, help="Number of correct prediction controls")
    parser.add_argument("--repeat", type=int, default=2, help="Repeated app-input predictions for dropout stability probe")
    parser.add_argument("--force", action="store_true", help="Re-run cases even if a per-video report already exists")
    args = parser.parse_args()
    if args.max_errors < 0 or args.controls < 0 or args.repeat < 2:
        parser.error("max-errors and controls must be >= 0; repeat must be >= 2")

    results_path = args.results if args.results.is_absolute() else ROOT / args.results
    metrics_path = args.metrics or (results_path.parent / "metrics.json")
    if not metrics_path.is_absolute():
        metrics_path = ROOT / metrics_path
    if not results_path.is_file() or not metrics_path.is_file():
        print(f"BLOCKED: expected both {results_path} and {metrics_path}", file=sys.stderr)
        return 2
    metrics = read_json(metrics_path)
    if metrics.get("evaluation_complete_for_official_split") is not True:
        print("BLOCKED: the paired parity batch requires a complete official validation evaluation.", file=sys.stderr)
        return 2
    if metrics.get("official_split_manifest_verified") is not True or metrics.get("successful_count") != metrics.get("expected_official_split_count"):
        print("BLOCKED: official manifest verification or successful inference count is incomplete.", file=sys.stderr)
        return 2

    # Ensure the reference source code being inspected corresponds to the same
    # pinned repository commit recorded by the completed validation evaluation.
    sys.path.insert(0, str(ROOT / "src"))
    from isl_recognition.app_config import Settings
    settings = Settings()
    repo = settings.ai4bharat_repo
    if repo is None or not repo.is_dir():
        print("BLOCKED: AI4BHARAT_REPO is not configured or does not exist.", file=sys.stderr)
        return 2
    expected_commit = "df348f4a640060d4d39f02556d9f96851e8c46c7"
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except Exception as exc:
        print(f"BLOCKED: could not verify AI4Bharat checkout HEAD: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if head != expected_commit:
        print(f"BLOCKED: expected AI4Bharat commit {expected_commit}, found {head}.", file=sys.stderr)
        return 2
    generation_source = repo / "generate_keypoints.py"
    if not generation_source.is_file():
        print(f"BLOCKED: upstream generator not found: {generation_source}", file=sys.stderr)
        return 2

    rows = read_csv(results_path)
    cases = select_cases(rows, args.max_errors, args.controls)
    if not cases:
        print("BLOCKED: no successful error/control samples were found in sample_results.csv", file=sys.stderr)
        return 2

    if args.data_root is None:
        data_root = settings.reference_video_dir
    else:
        data_root = args.data_root.expanduser()
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    summary_csv_rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        try:
            video = resolve_video_path(data_root, case["video_path"])
        except Exception as exc:
            report = {"status": "failed", "error": f"Path resolution: {type(exc).__name__}: {exc}"}
            reports.append(report)
            continue
        if not video.is_file():
            report = {"status": "failed", "error": f"Video not found: {video}"}
            reports.append(report)
            continue
        safe_stem = "_".join([case["case_type"], case["label"], video.stem]).replace(" ", "_")
        output_json = out_dir / f"{safe_stem}.json"
        output_log = out_dir / f"{safe_stem}.log"
        print(f"[{index}/{len(cases)}] {case['case_type'].upper():7s} {case['label']:12s} -> {video.name}", flush=True)
        if output_json.is_file() and not args.force:
            try:
                report = read_json(output_json)
                report["status"] = "success"
                report["resumed_existing_report"] = True
                reports.append(report)
                continue
            except Exception:
                pass
        command = [
            sys.executable, str(PARITY_SCRIPT), str(video),
            "--expected", case["label"],
            "--resize-width", "0",
            "--jpeg-quality", "90",
            "--repeat", str(args.repeat),
            "--output", str(output_json),
        ]
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, errors="replace")
        output_log.write_text(
            f"COMMAND: {subprocess.list2cmdline(command)}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}\nEXIT_CODE: {proc.returncode}\n",
            encoding="utf-8",
        )
        if proc.returncode == 0 and output_json.is_file():
            try:
                report = read_json(output_json)
                report["status"] = "success"
            except Exception as exc:
                report = {"status": "failed", "error": f"Could not read diagnostic JSON: {exc}"}
        else:
            report = {"status": "failed", "return_code": proc.returncode, "error": proc.stderr[-4000:]}
        reports.append(report)

    summary = summarize_reports(cases, reports)
    summary["source_metrics_path"] = str(metrics_path.resolve())
    summary["source_results_path"] = str(results_path.resolve())
    summary["data_root"] = str(data_root.resolve())
    summary["ai4bharat_repo_commit"] = head
    summary["elapsed_seconds"] = time.perf_counter() - started
    summary["app_path_settings"] = {"resize_width": "native", "jpeg_quality": 90, "repeat": args.repeat}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for case, report in zip(cases, reports):
        diag = report.get("feature_parity", {})
        summary_csv_rows.append({
            "case_type": case["case_type"],
            "label": case["label"],
            "baseline_prediction": case["baseline_prediction"],
            "baseline_confidence": case["baseline_confidence"],
            "video_path": case["video_path"],
            "status": report.get("status", "unknown"),
            "app_top1": report.get("app_pipeline_prediction_same_rng_seed", {}).get("top1_label"),
            "reference_top1": report.get("official_pipeline_prediction_same_rng_seed", {}).get("top1_label"),
            "app_top5_correct": any(x.get("label") == case["label"] for x in report.get("app_pipeline_prediction_same_rng_seed", {}).get("top5", [])),
            "reference_top5_correct": any(x.get("label") == case["label"] for x in report.get("official_pipeline_prediction_same_rng_seed", {}).get("top5", [])),
            "mean_absolute_feature_difference": diag.get("mean_absolute_feature_difference"),
            "p95_absolute_feature_difference": diag.get("p95_absolute_feature_difference"),
            "features_exactly_equal": diag.get("exactly_equal"),
        })
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_csv_rows[0]))
        writer.writeheader()
        writer.writerows(summary_csv_rows)

    print("\n========== PARITY BATCH SUMMARY ==========")
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2))
    print(f"\nDetailed reports: {out_dir}")
    return 0 if summary["failed_diagnostic_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
