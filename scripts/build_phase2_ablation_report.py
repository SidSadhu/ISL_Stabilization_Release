#!/usr/bin/env python3
"""Build the human-readable report and figures from a completed Phase 2 run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = (
    "app_direct_native",
    "resized_640_png",
    "resized_640_jpeg_q92",
    "upstream_original",
)
VARIANT_LABELS = {
    "app_direct_native": "App native",
    "resized_640_png": "640px PNG",
    "resized_640_jpeg_q92": "640px JPEG q92",
    "upstream_original": "Upstream modern adapter",
}
PAIR_LABELS = {
    "native_vs_resized_png": "Native → resized PNG",
    "resize_png_vs_jpeg_q92": "PNG → JPEG q92",
    "app_native_vs_upstream_modern_adapter": "App native → upstream adapter",
    "opencv_q92_vs_upstream_modern_adapter": "OpenCV q92 → upstream adapter",
}
GROUPS = ("pose", "hand1", "hand2")


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def build_report(summary: dict[str, Any], frame: pd.DataFrame, out_dir: Path) -> dict[str, Any]:
    if summary.get("status") != "completed" or summary.get("successful_count") != 77:
        raise ValueError("Phase 2 summary must report completed with 77 successful clips")
    if summary.get("failed_count") != 0 or summary.get("selected_count") != 77:
        raise ValueError("Phase 2 run must contain exactly 77 successful selected clips")
    if len(frame) != 77:
        raise ValueError(f"Expected 77 per-clip rows, got {len(frame)}")
    required = {"video_path", "expected_label", "status"}
    for variant in VARIANTS:
        required.update({f"{variant}_top1", f"{variant}_confidence", f"{variant}_correct"})
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Per-clip CSV is missing required columns: {missing}")
    if set(frame["status"].astype(str)) != {"success"}:
        raise ValueError("Per-clip CSV contains failed rows")
    if frame["video_path"].nunique() != 77:
        raise ValueError("Per-clip CSV does not contain 77 unique official videos")

    out_dir.mkdir(parents=True, exist_ok=True)
    y = frame["expected_label"].astype(str)
    reports = {}
    class_rows = []
    confusion_rows = []
    for variant in VARIANTS:
        pred = frame[f"{variant}_top1"].astype(str)
        stats = summary["recognition_by_variant"][variant]
        names = sorted(set(y) | set(pred))
        cm = confusion_matrix(y, pred, labels=names)
        class_report = classification_report(
            y, pred, labels=names, output_dict=True, zero_division=0
        )
        for label in names:
            row = class_report.get(label, {})
            support = int(row.get("support", 0))
            class_rows.append({
                "variant": variant,
                "label": label,
                "support": support,
                "precision": _finite(row.get("precision")),
                "recall": _finite(row.get("recall")),
                "f1": _finite(row.get("f1-score")),
            })
        for i, actual in enumerate(names):
            for j, predicted in enumerate(names):
                if i != j and int(cm[i, j]) > 0:
                    confusion_rows.append({
                        "variant": variant,
                        "actual": actual,
                        "predicted": predicted,
                        "count": int(cm[i, j]),
                    })
        reports[variant] = {
            "predicted_count": stats.get("predicted_count"),
            "top1_correct_count": stats.get("top1_correct_count"),
            "top1_accuracy": stats.get("top1_accuracy"),
            "top5_correct_count": stats.get("top5_correct_count"),
            "top5_accuracy": stats.get("top5_accuracy"),
            "macro_f1": class_report.get("macro avg", {}).get("f1-score"),
            "weighted_f1": class_report.get("weighted avg", {}).get("f1-score"),
            "confidence": stats.get("confidence"),
        }

    pd.DataFrame(class_rows).to_csv(out_dir / "phase2_class_metrics.csv", index=False)
    confusion_frame = pd.DataFrame(confusion_rows)
    if not confusion_frame.empty:
        confusion_frame.sort_values(["variant", "count", "actual", "predicted"], ascending=[True, False, True, True]).to_csv(
            out_dir / "phase2_confusions.csv", index=False
        )
    else:
        pd.DataFrame(columns=["variant", "actual", "predicted", "count"]).to_csv(
            out_dir / "phase2_confusions.csv", index=False
        )

    pair_mad = summary["feature_mad_by_pair_and_group"]
    pair_agreement = summary["top1_agreement_counts"]
    pair_rows = []
    for pair, label in PAIR_LABELS.items():
        pair_row = {"pair": pair, "comparison": label, "top1_agreement_count": pair_agreement.get(pair)}
        for group in GROUPS:
            pair_row[f"{group}_mad_mean"] = pair_mad.get(pair, {}).get(group, {}).get("mean")
            pair_row[f"{group}_mad_p90"] = pair_mad.get(pair, {}).get(group, {}).get("p90")
        pair_rows.append(pair_row)
    pd.DataFrame(pair_rows).to_csv(out_dir / "phase2_pair_comparisons.csv", index=False)

    # Figure 1: Top-1 and Top-5 by extraction path.
    names = [VARIANT_LABELS[v] for v in VARIANTS]
    top1 = [reports[v]["top1_accuracy"] for v in VARIANTS]
    top5 = [reports[v]["top5_accuracy"] for v in VARIANTS]
    x = np.arange(len(names))
    width = 0.36
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.bar(x - width / 2, top1, width, label="Top-1")
    ax.bar(x + width / 2, top5, width, label="Top-5")
    ax.set_xticks(x, names, rotation=12, ha="right")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Accuracy")
    ax.set_title("Phase 2 recognition by feature-generation path (77 validation clips)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "phase2_variant_accuracy.png", dpi=180)
    plt.close(fig)

    # Figure 2: feature MAD by controlled pair and feature family.
    pair_names = list(PAIR_LABELS)
    x = np.arange(len(pair_names))
    width = 0.24
    fig, ax = plt.subplots(figsize=(11, 5.4))
    for offset, group in enumerate(GROUPS):
        values = [pair_mad[pair][group]["mean"] for pair in pair_names]
        ax.bar(x + (offset - 1) * width, values, width, label=group)
    ax.set_xticks(x, [PAIR_LABELS[p] for p in pair_names], rotation=15, ha="right")
    ax.set_ylabel("Mean absolute difference (scaled coordinate units)")
    ax.set_title("Feature difference by isolated pipeline stage")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(title="Feature group")
    fig.tight_layout()
    fig.savefig(out_dir / "phase2_feature_mad_by_pair.png", dpi=180)
    plt.close(fig)

    # Figure 3: top-1 agreement on each paired comparison.
    agreements = [pair_agreement.get(pair, 0) / 77 for pair in pair_names]
    fig, ax = plt.subplots(figsize=(10, 5.0))
    ax.barh([PAIR_LABELS[p] for p in pair_names][::-1], agreements[::-1])
    ax.set_xlim(0, 1)
    ax.set_xlabel("Fraction of clips with identical top-1 output")
    ax.set_title("Top-1 prediction agreement by controlled comparison")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_dir / "phase2_pair_prediction_agreement.png", dpi=180)
    plt.close(fig)

    report = {
        "campaign": "ISL Phase 2 — 77-clip feature provenance and preprocessing ablation",
        "status": "completed",
        "selected_count": 77,
        "successful_count": 77,
        "failed_count": 0,
        "runtime": summary.get("runtime"),
        "assets": summary.get("assets"),
        "methodology": summary.get("methodology"),
        "limitations": summary.get("limitations"),
        "recognition_by_variant": reports,
        "top1_agreement_counts": pair_agreement,
        "feature_mad_by_pair_and_group": pair_mad,
        "output_files": [
            "phase2_ablation_analysis.json",
            "phase2_class_metrics.csv",
            "phase2_confusions.csv",
            "phase2_pair_comparisons.csv",
            "phase2_variant_accuracy.png",
            "phase2_feature_mad_by_pair.png",
            "phase2_pair_prediction_agreement.png",
            "PHASE2_ABLATION_REPORT.md",
        ],
    }
    (out_dir / "phase2_ablation_analysis.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    lines = [
        "# Phase 2 — 77-Clip Feature Provenance and Preprocessing Ablation",
        "",
        "**Status:** Completed; all 77 official validation clips passed the per-clip diagnostic.",
        "",
        "## Recognition by path",
        "",
        "| Path | Top-1 | Top-5 | Macro F1 |",
        "|---|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        stats = reports[variant]
        lines.append(
            f"| {VARIANT_LABELS[variant]} | {stats['top1_correct_count']}/77 ({stats['top1_accuracy']:.2%}) "
            f"| {stats['top5_correct_count']}/77 ({stats['top5_accuracy']:.2%}) | {stats['macro_f1']:.4f} |"
        )
    lines.extend([
        "",
        "## Interpretation guardrails",
        "",
        "- PNG vs JPEG q92 estimates the compression-only effect after the same OpenCV resize.",
        "- Native vs PNG estimates the 640-pixel resize effect; OpenCV resize is not pixel-identical to browser Canvas.",
        "- App-native vs upstream-modern-adapter compares code paths under the current MediaPipe version, not historical `upper_body_only=True` detector parity.",
        "- Do not change the checkpoint, class order, 134-D feature contract, 169-frame input, confidence threshold or IoT state from these results without a separate reviewed decision.",
        "- The 77-item validation set has low per-class support and is not independent-signer or production-readiness evidence.",
        "",
        "## Runtime and provenance",
        "",
        f"- Runtime: `{json.dumps(report['runtime'], sort_keys=True)}`",
        f"- Asset fingerprints: `{json.dumps(report['assets'], sort_keys=True)}`",
        "",
        "The raw `summary.json`, `per_clip_summary.csv`, 77 per-clip JSON records and per-clip logs remain the source evidence. This generated report is a derived presentation layer.",
    ])
    (out_dir / "PHASE2_ABLATION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--per-clip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8-sig"))
    per_clip = pd.read_csv(args.per_clip)
    out = args.out_dir.expanduser().resolve()
    report = build_report(summary, per_clip, out)
    print(json.dumps({
        "status": report["status"],
        "sample_count": report["selected_count"],
        "recognition_by_variant": report["recognition_by_variant"],
        "top1_agreement_counts": report["top1_agreement_counts"],
        "outputs": report["output_files"],
        "out_dir": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
