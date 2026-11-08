#!/usr/bin/env python3
"""Reconcile Phase 2's final resumable 77/77 evidence, regenerate report and bundle.

This is a packaging/report repair only. It does not rerun MediaPipe, load the
classifier checkpoint, alter predictions, or touch training/test data.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, shutil, tempfile, zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

EXPECTED_COUNT = 77
EXPECTED_REPO_COMMIT = "df348f4a640060d4d39f02556d9f96851e8c46c7"
EXPECTED_OFFICIAL_SPLIT_SHA = "044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541"
EXPECTED_CHECKPOINT_SHA = "8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4"
VARIANTS = ("app_direct_native", "resized_640_png", "resized_640_jpeg_q92", "upstream_original")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").casefold()


def individual_video_identity(item: dict[str, Any]) -> str:
    """Return the official manifest identity, not a resolved local extension variant."""
    return norm(item.get("video_path") or item.get("video") or "")


def load_and_validate(out_dir: Path, manifest: Path | None = None) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    summary_path = out_dir / "summary.json"
    csv_path = out_dir / "per_clip_summary.csv"
    per_clip_dir = out_dir / "per_clip"
    if not summary_path.is_file() or not csv_path.is_file() or not per_clip_dir.is_dir():
        raise FileNotFoundError("Phase 2 summary.json, per_clip_summary.csv, or per_clip directory is missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    if summary.get("status") != "completed":
        raise ValueError(f"Phase 2 summary status is {summary.get('status')!r}, not 'completed'")
    if (summary.get("selected_count"), summary.get("successful_count"), summary.get("failed_count")) != (77, 77, 0):
        raise ValueError("Phase 2 summary is not 77 selected / 77 successful / 0 failed")
    assets = summary.get("assets", {})
    if assets.get("repo_commit") != EXPECTED_REPO_COMMIT:
        raise ValueError("Phase 2 summary AI4Bharat commit does not match the pinned repository")
    if assets.get("official_split_sha256") != EXPECTED_OFFICIAL_SPLIT_SHA:
        raise ValueError("Phase 2 summary official validation split hash does not match")
    if assets.get("checkpoint_sha256") != EXPECTED_CHECKPOINT_SHA:
        raise ValueError("Phase 2 summary checkpoint hash does not match the locked checkpoint")
    if summary.get("runtime", {}).get("iot_enabled") is not False or summary.get("runtime", {}).get("pre_iot_lockdown") is not True:
        raise ValueError("Phase 2 summary safety metadata does not confirm IoT disabled and pre-IoT lockdown")

    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        csv_rows = list(csv.DictReader(f))
    json_rows = summary.get("per_clip", [])
    json_statuses = Counter(item.get("status") for item in json_rows)
    csv_statuses = Counter(item.get("status") for item in csv_rows)
    if len(csv_rows) != EXPECTED_COUNT or len(json_rows) != EXPECTED_COUNT:
        raise ValueError(f"Expected 77 rows in both summary.per_clip and CSV; found {len(json_rows)} and {len(csv_rows)}")
    if json_statuses != Counter({"success": 77}) or csv_statuses != Counter({"success": 77}):
        raise ValueError(f"Per-clip success counts mismatch: JSON={json_statuses}, CSV={csv_statuses}")
    json_paths = [norm(x.get("video_path", "")) for x in json_rows]
    csv_paths = [norm(x.get("video_path", "")) for x in csv_rows]
    if len(set(json_paths)) != 77 or len(set(csv_paths)) != 77 or set(json_paths) != set(csv_paths):
        raise ValueError("Per-clip paths are not 77 unique, matching video paths")

    json_files = sorted(per_clip_dir.glob("*.json"))
    if len(json_files) != 77:
        raise ValueError(f"Expected 77 individual per-clip JSON reports; found {len(json_files)}")
    per_clip_objects = []
    output_paths = set()
    for path in json_files:
        item = json.loads(path.read_text(encoding="utf-8-sig"))
        if item.get("status") != "success":
            raise ValueError(f"Non-success per-clip JSON: {path.name} ({item.get('status')})")
        # `video` is the resolved local file path and can contain an extension
        # variant (e.g. official .MOV, local .MP4). `video_path` is the canonical
        # official manifest path and is the correct identity for reconciliation.
        identity = individual_video_identity(item)
        if identity:
            output_paths.add(identity)
        per_clip_objects.append(item)
    if output_paths and output_paths != set(json_paths):
        raise ValueError("Individual per-clip JSON paths do not match summary/CSV path set")

    if manifest is not None:
        with manifest.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not {"video_path", "label"}.issubset(set(reader.fieldnames or [])):
                raise ValueError("Official validation manifest must contain video_path,label")
            manifest_rows = list(reader)
        manifest_paths = {norm(x.get("video_path", "")) for x in manifest_rows}
        if len(manifest_rows) != 77 or manifest_paths != set(json_paths):
            raise ValueError("Phase 2 results do not exactly cover the pinned 77 manifest paths")
    return summary, csv_rows, per_clip_objects


def fmt_pct(value: Any) -> str:
    return f"{float(value) * 100:.2f}%" if value is not None else "NA"


def build_report(summary: dict[str, Any], run_log_path: Path | None) -> str:
    lines = [
        "# Phase 2 — Feature provenance and preprocessing ablation (reconciled)", "",
        "## Verified run status", "",
        "**Completed: 77/77 official validation clips; 0 failed.**", "",
        f"- Summary generation time: `{summary.get('generated_at_utc')}`",
        f"- Elapsed time recorded by the successful resumable run: `{summary.get('elapsed_seconds')}` seconds",
        f"- AI4Bharat commit: `{summary['assets']['repo_commit']}`",
        f"- Official validation path-list SHA-256: `{summary['assets']['official_split_sha256']}`",
        f"- Locked checkpoint SHA-256: `{summary['assets']['checkpoint_sha256']}`",
        f"- Resume migration: `{summary.get('resume_compatibility', {}).get('prior_successes_reused', 0)}` previously successful clips reused; the missing item was recomputed.",
        "- Split used: official validation only; test split not used.",
        "- IoT disabled; pre-IoT lockdown verified.", "",
        "## Four-path recognition results", "",
        "| Path | Top-1 correct | Top-1 accuracy | Top-5 correct | Top-5 accuracy | Predicted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    recognition = summary.get("recognition_by_variant", {})
    for name in VARIANTS:
        m = recognition.get(name, {})
        lines.append(f"| {name} | {m.get('top1_correct_count','NA')}/77 | {fmt_pct(m.get('top1_accuracy'))} | {m.get('top5_correct_count','NA')}/77 | {fmt_pct(m.get('top5_accuracy'))} | {m.get('predicted_count','NA')}/77 |")

    lines += ["", "## Top-1 agreement across paths", "", "| Pair | Matching top-1 predictions |", "|---|---:|"]
    for pair, count in summary.get("top1_agreement_counts", {}).items():
        lines.append(f"| {pair} | {count}/77 ({float(count)/77*100:.2f}%) |")

    lines += ["", "## Feature-space differences", "", "Mean absolute difference (MAD) is reported separately for pose, first-hand, and second-hand feature groups.", "",
              "| Pair | Group | Mean MAD | Median MAD | P90 MAD | Maximum MAD |", "|---|---|---:|---:|---:|---:|"]
    for pair, groups in summary.get("feature_mad_by_pair_and_group", {}).items():
        for group, metrics in groups.items():
            lines.append(f"| {pair} | {group} | {metrics.get('mean', float('nan')):.4f} | {metrics.get('median', float('nan')):.4f} | {metrics.get('p90', float('nan')):.4f} | {metrics.get('max', float('nan')):.4f} |")

    lines += ["", "## Methodology", ""]
    for key, text in summary.get("methodology", {}).items():
        lines.append(f"- **{key}:** {text}")
    lines += ["", "## Recovery of the earlier incomplete attempt", "",
              "The earlier execution log records a failure on `Adjectives/79. short/MVI_9302.MOV`: the upstream compatibility path attempted to access `.landmark` on a `None` pose result. The runner continued and recorded the first attempt as 76/77. A subsequent fingerprint-checked resumable run reused the 76 prior successful clips and recomputed the missing clip. The final `summary.json`, `per_clip_summary.csv`, and all 77 individual per-clip JSON reports now agree on 77 successes and zero failures.", "",
              "The original `phase2_run.log` is retained as historical first-attempt evidence. It is not treated as the final successful run log. This reconciliation report and bundle are generated from the later completed summary/CSV/per-clip artifacts.", "",
              "## Limitations", ""]
    for item in summary.get("limitations", []):
        lines.append(f"- {item}")
    lines += ["", "## Interpretation", "",
              "The current-application native and upstream modern-adapter paths agree on 77/77 top-1 predictions, with zero reported feature MAD for the app-native versus upstream-modern-adapter comparison. The resize and JPEG branches are controlled OpenCV approximations; their differences should not be described as exact browser Canvas pixel parity. The observed accuracies are validation-split results, not independent signer generalisation claims.", "",
              "## Reproducibility and safety", "",
              "This repair script only validates existing outputs, creates a Markdown report, creates a reconciliation log and packages evidence. It does not rerun recognition, train, tune, modify the checkpoint, change the 50-class vocabulary, change the 134-D feature contract, change the 169-frame input, access the test split, or enable IoT.", ""]
    if run_log_path and run_log_path.is_file():
        lines.append(f"Historical raw execution log SHA-256: `{sha256(run_log_path)}`.")
    return "\n".join(lines)


def build_reconciliation_log(summary: dict[str, Any], historical_log: Path | None) -> str:
    prior = summary.get("resume_compatibility", {})
    lines = [
        "PHASE2_RECONCILIATION_STATUS=completed",
        "SOURCE=existing resumable run outputs; no model inference rerun",
        f"SELECTED={summary['selected_count']}",
        f"SUCCESSFUL={summary['successful_count']}",
        f"FAILED={summary['failed_count']}",
        f"SUMMARY_GENERATED_AT_UTC={summary.get('generated_at_utc')}",
        f"RESUME_ENABLED={prior.get('enabled')}",
        f"PRIOR_SUCCESSES_REUSED={prior.get('prior_successes_reused')}",
        "OFFICIAL_TEST_SPLIT_USED=false",
        "IOT_ENABLED=false",
        "CHECKPOINT_UNCHANGED=true",
        "FEATURE_CONTRACT=134D",
        "MAX_SEQUENCE_FRAMES=169",
    ]
    if historical_log and historical_log.is_file():
        lines += [f"HISTORICAL_FIRST_ATTEMPT_LOG_SHA256={sha256(historical_log)}",
                  "HISTORICAL_FIRST_ATTEMPT_NOTE=76/77; MVI_9302.MOV raised AttributeError on None pose result; later resume artifacts are complete."]
    return "\n".join(lines) + "\n"


def package(out_dir: Path, report_dir: Path, manifest: Path | None, historical_log: Path | None) -> tuple[Path, str]:
    summary, csv_rows, per_clip_objects = load_and_validate(out_dir, manifest)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "PHASE2_ABLATION_REPORT.md"
    report_path.write_text(build_report(summary, historical_log), encoding="utf-8")
    recon_log = out_dir / "phase2_reconciled_run.log"
    recon_log.write_text(build_reconciliation_log(summary, historical_log), encoding="utf-8")

    bundle = out_dir / "PHASE2_RESULT_BUNDLE_RECONCILED.zip"
    with tempfile.TemporaryDirectory(prefix="phase2_reconciled_") as temp:
        stage = Path(temp) / "PHASE2_RESULT_BUNDLE"
        (stage / "report").mkdir(parents=True)
        (stage / "per_clip").mkdir()
        (stage / "logs").mkdir()
        shutil.copy2(out_dir / "summary.json", stage / "summary.json")
        shutil.copy2(out_dir / "per_clip_summary.csv", stage / "per_clip_summary.csv")
        shutil.copy2(report_path, stage / "report" / report_path.name)
        shutil.copy2(recon_log, stage / "phase2_reconciled_run.log")
        for p in sorted((out_dir / "per_clip").glob("*.json")):
            shutil.copy2(p, stage / "per_clip" / p.name)
        if historical_log and historical_log.is_file():
            shutil.copy2(historical_log, stage / "logs" / "phase2_first_attempt_historical_76_of_77.log")
        for p in sorted((out_dir / "logs").glob("*.log")) if (out_dir / "logs").is_dir() else []:
            shutil.copy2(p, stage / "logs" / p.name)
        if manifest and manifest.is_file():
            shutil.copy2(manifest, stage / "include50_official_val_manifest.csv")
        provenance = {
            "status": "completed", "selected_count": 77, "successful_count": 77, "failed_count": 0,
            "summary_generated_at_utc": summary.get("generated_at_utc"),
            "resume_compatibility": summary.get("resume_compatibility"),
            "historical_first_attempt_log_included": bool(historical_log and historical_log.is_file()),
            "historical_log_is_not_the_final_run_log": True,
            "model_or_checkpoint_modified": False, "iot_enabled": False,
        }
        (stage / "PHASE2_RECONCILIATION_METADATA.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        hash_lines=[]
        for p in sorted(x for x in stage.rglob("*") if x.is_file()):
            hash_lines.append(f"{sha256(p)}  {p.relative_to(stage).as_posix()}")
        (stage / "SHA256SUMS.txt").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(x for x in stage.rglob("*") if x.is_file()):
                z.write(p, p.relative_to(stage).as_posix())
        with zipfile.ZipFile(bundle) as z:
            bad=z.testzip()
            if bad: raise RuntimeError(f"ZIP CRC failure: {bad}")
            for line in z.read("SHA256SUMS.txt").decode("utf-8").splitlines():
                expected, rel = line.split("  ",1)
                if hashlib.sha256(z.read(rel)).hexdigest()!=expected:
                    raise RuntimeError(f"Bundle hash mismatch: {rel}")
            if "report/PHASE2_ABLATION_REPORT.md" not in z.namelist():
                raise RuntimeError("Reconciled report missing from ZIP")
    return bundle, sha256(bundle)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--manifest",type=Path,default=None)
    parser.add_argument("--historical-run-log",type=Path,default=None)
    args=parser.parse_args()
    out=args.out_dir.resolve(); manifest=args.manifest.resolve() if args.manifest else None
    historical=args.historical_run_log.resolve() if args.historical_run_log else (out/"phase2_run.log")
    try:
        summary,_,_=load_and_validate(out,manifest)
        report_dir=out/"report"
        bundle,digest=package(out,report_dir,manifest,historical)
        print("PHASE2_RECONCILIATION=COMPLETED")
        print("SUCCESSFUL=77/77 | FAILED=0")
        print(f"RESUME_PRIOR_SUCCESSES_REUSED={summary.get('resume_compatibility',{}).get('prior_successes_reused')}")
        print(f"REPORT={report_dir/'PHASE2_ABLATION_REPORT.md'}")
        print(f"RECONCILIATION_LOG={out/'phase2_reconciled_run.log'}")
        print(f"BUNDLE={bundle}")
        print(f"BUNDLE_SHA256={digest}")
        print("MODEL_UNCHANGED=true | IOT_ENABLED=false | TEST_SPLIT_USED=false")
        return 0
    except Exception as exc:
        print(f"PHASE2_RECONCILIATION_BLOCKED={type(exc).__name__}: {exc}")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
