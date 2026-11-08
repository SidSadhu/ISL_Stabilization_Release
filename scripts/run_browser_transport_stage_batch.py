#!/usr/bin/env python
"""Run the legacy eight-clip browser-transport pilot only.

This runner consumes an existing pilot summary CSV and is not the full official
77-video Phase 1 benchmark. Use the browser-native capture/evaluation scripts
for that complete validation campaign.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DIAGNOSTIC = SCRIPTS / "diagnose_browser_transport_stages.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from video_path_utils import resolve_video_path  # noqa: E402


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in reports if r.get("status") == "success"]
    pair_names = [
        "app_direct_native_vs_upstream",
        "app_direct_native_vs_resized_png",
        "resized_png_vs_jpeg_q72",
        "resized_png_vs_jpeg_q92",
        "jpeg_q72_vs_jpeg_q92",
        "browser_q72_vs_upstream",
        "browser_q92_vs_upstream",
    ]
    summary: dict[str, Any] = {
        "selected_count": len(reports),
        "successful_count": len(valid),
        "failed_count": len(reports) - len(valid),
        "top1_agreement_counts": {},
        "median_feature_mad_by_pair_and_group": {},
        "per_clip": [],
    }
    for pair in pair_names:
        summary["top1_agreement_counts"][pair] = sum(
            bool(r.get("top1_agreement_by_pair", {}).get(pair, False)) for r in valid
        )
        summary["median_feature_mad_by_pair_and_group"][pair] = {}
        for group in ("pose", "hand1", "hand2"):
            vals = [
                r["feature_comparisons_after_interpolation"][pair]["groups"][group]["mean_absolute_difference"]
                for r in valid
            ]
            summary["median_feature_mad_by_pair_and_group"][pair][group] = {
                "median_across_clips": median(vals) if vals else None,
                "max_across_clips": max(vals) if vals else None,
            }

    top1_fields = (
        "app_direct_native", "resized_640_png", "resized_640_jpeg_q72",
        "resized_640_jpeg_q92", "upstream_original",
    )
    for report in reports:
        preds = report.get("predictions_same_seed", {})
        summary["per_clip"].append({
            "case_type": report.get("case_type"),
            "label": report.get("label"),
            "video_path": report.get("video_path"),
            "status": report.get("status"),
            **{f"{key}_top1": preds.get(key, {}).get("top1_label") for key in top1_fields},
            "native_vs_upstream_mad": report.get("feature_comparisons_after_interpolation", {}).get("app_direct_native_vs_upstream", {}).get("overall", {}).get("mean_absolute_difference"),
            "resize_only_mad": report.get("feature_comparisons_after_interpolation", {}).get("app_direct_native_vs_resized_png", {}).get("overall", {}).get("mean_absolute_difference"),
            "jpeg72_after_resize_mad": report.get("feature_comparisons_after_interpolation", {}).get("resized_png_vs_jpeg_q72", {}).get("overall", {}).get("mean_absolute_difference"),
            "jpeg92_after_resize_mad": report.get("feature_comparisons_after_interpolation", {}).get("resized_png_vs_jpeg_q92", {}).get("overall", {}).get("mean_absolute_difference"),
        })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-summary", type=Path, default=Path("outputs/validation_parity_pilot/summary.csv"))
    parser.add_argument("--metrics", type=Path, default=Path("outputs/include50_validation_baseline/metrics.json"))
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/browser_transport_stages"))
    parser.add_argument("--resize-width", type=int, default=640)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.resize_width < 1:
        parser.error("--resize-width must be positive")

    pilot_path = args.pilot_summary if args.pilot_summary.is_absolute() else ROOT / args.pilot_summary
    metrics_path = args.metrics if args.metrics.is_absolute() else ROOT / args.metrics
    if not pilot_path.is_file() or not metrics_path.is_file():
        print("BLOCKED: pilot summary or complete validation metrics file not found.", file=sys.stderr)
        return 2
    metrics = read_json(metrics_path)
    if metrics.get("evaluation_complete_for_official_split") is not True:
        print("BLOCKED: official validation is not complete.", file=sys.stderr)
        return 2
    if metrics.get("official_split_manifest_verified") is not True or metrics.get("successful_count") != metrics.get("expected_official_split_count"):
        print("BLOCKED: official validation manifest/count checks did not pass.", file=sys.stderr)
        return 2

    sys.path.insert(0, str(ROOT / "src"))
    from isl_recognition.app_config import Settings
    settings = Settings()
    repo = settings.ai4bharat_repo
    if repo is None or not repo.is_dir():
        print("BLOCKED: AI4BHARAT_REPO is not configured.", file=sys.stderr)
        return 2
    expected_commit = "df348f4a640060d4d39f02556d9f96851e8c46c7"
    try:
        actual_commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except Exception as exc:
        print(f"BLOCKED: cannot verify AI4Bharat commit: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if actual_commit != expected_commit:
        print(f"BLOCKED: expected AI4Bharat {expected_commit}, found {actual_commit}.", file=sys.stderr)
        return 2
    if not DIAGNOSTIC.is_file():
        print(f"BLOCKED: diagnostic script is missing: {DIAGNOSTIC}", file=sys.stderr)
        return 2

    data_root = args.data_root or settings.reference_video_dir
    if data_root is None:
        print("BLOCKED: reference video directory is not configured.", file=sys.stderr)
        return 2
    data_root = Path(data_root)
    cases = read_csv(pilot_path)
    if not cases:
        print("BLOCKED: pilot summary contains no diagnostic cases.", file=sys.stderr)
        return 2
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, case in enumerate(cases, 1):
        kind = case.get("case_type", "case")
        label = case.get("label", "")
        relative = case.get("video_path", "")
        video = resolve_video_path(data_root, relative)
        stem = f"{kind}_{label}_{video.stem}".replace(" ", "_")
        output_json = out_dir / f"{stem}.json"
        output_log = out_dir / f"{stem}.log"
        print(f"[{index}/{len(cases)}] {kind.upper():7s} {label:12s} -> {video.name}", flush=True)

        if output_json.is_file() and not args.force:
            try:
                report = read_json(output_json)
                report.update({"status": "success", "case_type": kind, "label": label, "video_path": relative, "resumed_existing_report": True})
                reports.append(report)
                continue
            except Exception:
                pass
        if not video.is_file():
            reports.append({"status": "failed", "case_type": kind, "label": label, "video_path": relative, "error": f"Video not found: {video}"})
            continue

        command = [
            sys.executable, str(DIAGNOSTIC), str(video),
            "--expected", label,
            "--resize-width", str(args.resize_width),
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
                report.update({"status": "success", "case_type": kind, "label": label, "video_path": relative})
            except Exception as exc:
                report = {"status": "failed", "case_type": kind, "label": label, "video_path": relative, "error": f"Invalid diagnostic JSON: {exc}"}
        else:
            report = {"status": "failed", "case_type": kind, "label": label, "video_path": relative, "error": proc.stderr[-5000:], "return_code": proc.returncode}
        reports.append(report)

    summary = aggregate(reports)
    summary.update({
        "diagnostic_type": "browser_capture_resize_and_jpeg_transport_ablation",
        "ai4bharat_repo_commit": actual_commit,
        "official_validation_metrics": str(metrics_path.resolve()),
        "pilot_source_summary": str(pilot_path.resolve()),
        "data_root": str(data_root.resolve()),
        "resize_width": args.resize_width,
        "resize_method": "OpenCV INTER_LINEAR; approximation of browser canvas drawImage",
        "jpeg_qualities": [72, 92],
        "elapsed_seconds": time.perf_counter() - started,
        "warning": "Modern MediaPipe first-25-point compatibility; not exact historic upper_body_only parity. Offline resize is an approximation of browser Canvas.",
    })
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    fields = ["case_type", "label", "video_path", "status"] + [
        "app_direct_native_top1", "resized_640_png_top1", "resized_640_jpeg_q72_top1",
        "resized_640_jpeg_q92_top1", "upstream_original_top1", "native_vs_upstream_mad",
        "resize_only_mad", "jpeg72_after_resize_mad", "jpeg92_after_resize_mad",
    ]
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary["per_clip"])
    print("\n========== BROWSER TRANSPORT SUMMARY ==========")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_clip"}, indent=2))
    print(f"\nDetailed reports: {out_dir}")
    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
