#!/usr/bin/env python3
"""Run a matched-seed dropout A/B study on cached official INCLUDE-50 validation features.

A = upstream unconditional functional dropout: F.dropout(x, p=0.2)
B = evaluation-aware dropout: F.dropout(x, p=0.2, training=self.training)

No project source files, checkpoint, feature cache, labels, or IoT settings are modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

PINNED_REPO_COMMIT = "df348f4a640060d4d39f02556d9f96851e8c46c7"
OFFICIAL_VAL_LIST_SHA256 = "044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541"
EXPECTED_CHECKPOINT_SHA256 = "8644dc4793b8f648af5c3ad42f0c5d5eeb86950cf4cbb2e2aae6e3cc678447b4"
EXPECTED_FEATURE_CACHE_SHA256 = "5678243185376d1f46d9bd95ea5a76c1a3c70186090793e490047a5953113e01"
# The only expected differences between these two source versions are the final
# functional-dropout line inside Transformer.forward(). Both variants are
# overridden at runtime below so that the two behaviours can be tested side by side.
ALLOWED_TRANSFORMER_SOURCE_SHA256 = {
    "4304ecf397b3afc1a142ea6496799a0c06f1ce5071a4b1e6925a2e05c57384fc",
    "fb04fd7fec5fe8f53c159762f471dc2e6729fa6d18f981ddd9c51b66c4da7945",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify_repo(repo: Path) -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().lower()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Cannot verify Git HEAD for AI4Bharat repository: {repo}\n{exc}") from exc
    if commit != PINNED_REPO_COMMIT:
        raise RuntimeError(
            f"AI4Bharat HEAD is {commit}; expected pinned commit {PINNED_REPO_COMMIT}. "
            "Do not run this comparison against a different upstream revision."
        )

    transformer_path = repo / "models" / "transformer.py"
    if not transformer_path.is_file():
        raise FileNotFoundError(f"Missing upstream model file: {transformer_path}")
    transformer_sha = sha256_file(transformer_path)
    if transformer_sha not in ALLOWED_TRANSFORMER_SOURCE_SHA256:
        raise RuntimeError(
            "models/transformer.py does not match either known source version used in the "
            "uploaded A/B campaign. Refusing to run because unrelated local edits could "
            f"confound the comparison. SHA256={transformer_sha}"
        )

    val_list_path = repo / "train_test_paths" / "include50_val.txt"
    if not val_list_path.is_file():
        raise FileNotFoundError(f"Missing official validation path list: {val_list_path}")
    val_list_sha = sha256_file(val_list_path)
    if val_list_sha != OFFICIAL_VAL_LIST_SHA256:
        raise RuntimeError(
            f"Official validation path-list SHA256 mismatch: {val_list_sha}; "
            f"expected {OFFICIAL_VAL_LIST_SHA256}."
        )
    return commit, transformer_sha


def load_labels(label_map_path: Path) -> tuple[dict[str, int], dict[int, str]]:
    raw = json.loads(label_map_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Label map must be a JSON object mapping labels to class indices.")
    label_to_idx = {str(k): int(v) for k, v in raw.items()}
    if len(label_to_idx) != 50 or sorted(label_to_idx.values()) != list(range(50)):
        raise ValueError("Expected the canonical 50-class label map with indices 0..49.")
    if label_to_idx.get("court") != 8:
        raise ValueError("Canonical label-order guard failed: court must map to index 8.")
    return label_to_idx, {v: k for k, v in label_to_idx.items()}


def validate_cache(npz_path: Path, metadata_path: Path, label_to_idx: dict[str, int]):
    cache_sha = sha256_file(npz_path)
    if cache_sha != EXPECTED_FEATURE_CACHE_SHA256:
        raise RuntimeError(
            f"Feature-cache SHA256 mismatch: {cache_sha}; expected {EXPECTED_FEATURE_CACHE_SHA256}. "
            "This runner is locked to the exact 77-tensor cache used in the uploaded A/B report."
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("complete") is not True or metadata.get("split") != "val":
        raise ValueError("Cache metadata must declare complete=true and split='val'.")
    if metadata.get("expected_count") != 77 or metadata.get("cached_count") != 77 or metadata.get("failed_count") != 0:
        raise ValueError("Expected exactly 77 cached validation samples and zero failures.")
    if metadata.get("ai4bharat_commit") != PINNED_REPO_COMMIT:
        raise ValueError("Cache metadata points to a different AI4Bharat repository commit.")
    if metadata.get("official_validation_path_list_sha256") != OFFICIAL_VAL_LIST_SHA256:
        raise ValueError("Cache metadata does not match the pinned official validation list.")
    records = metadata.get("records")
    if not isinstance(records, list) or len(records) != 77:
        raise ValueError("Cache metadata must contain 77 ordered sample records.")

    features_npz = np.load(npz_path, allow_pickle=False)
    expected_keys = [f"sample_{i:03d}" for i in range(1, 78)]
    if list(features_npz.files) != expected_keys:
        raise ValueError("Feature-cache keys/order differ from sample_001 ... sample_077.")

    samples = []
    for i, record in enumerate(records, start=1):
        key = f"sample_{i:03d}"
        label = str(record.get("label", ""))
        if label not in label_to_idx:
            raise ValueError(f"Unknown label in cache metadata for {key}: {label!r}")
        x = np.asarray(features_npz[key])
        if x.shape != (169, 134) or x.dtype != np.float32:
            raise ValueError(f"{key} must be float32 with shape (169,134), got {x.dtype} {x.shape}.")
        if not np.isfinite(x).all():
            raise ValueError(f"Non-finite feature values found in {key}.")
        samples.append({
            "key": key,
            "video_path": str(record.get("video_path", "")),
            "label": label,
            "label_index": label_to_idx[label],
            "features": np.array(x, copy=True),
        })
    features_npz.close()
    return samples, metadata, cache_sha


def extract_state_dict(checkpoint: Any) -> dict[str, torch.Tensor]:
    if not isinstance(checkpoint, dict):
        raise RuntimeError(f"Unexpected checkpoint type: {type(checkpoint)}")
    if "state_dict" in checkpoint:
        state = checkpoint["state_dict"]
    elif "model_state_dict" in checkpoint:
        state = checkpoint["model_state_dict"]
    elif isinstance(checkpoint.get("model"), dict):
        state = checkpoint["model"]
    else:
        state = checkpoint
    return {str(k).removeprefix("module."): v for k, v in state.items()}


def load_model(repo: Path, checkpoint_path: Path, device: torch.device):
    if sha256_file(checkpoint_path) != EXPECTED_CHECKPOINT_SHA256:
        raise RuntimeError(
            "Checkpoint SHA256 differs from the checkpoint used for the uploaded A/B campaign. "
            "No comparison was run."
        )

    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    # Match the app adapter's compatibility bridge for the pinned INCLUDE code.
    import transformers
    from transformers.models.bert.modeling_bert import BertLayer
    transformers.BertLayer = BertLayer
    from configs import TransformerConfig
    from models.transformer import Transformer

    model = Transformer(TransformerConfig(size="small"), n_classes=50).to(device)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model.load_state_dict(extract_state_dict(checkpoint), strict=True)
    model.eval()
    if model.training:
        raise RuntimeError("Model must be in eval mode before either dropout condition is measured.")
    return model


def forward_original_unconditional(self, x: torch.Tensor) -> torch.Tensor:
    """Exact inference path of upstream F.dropout(x, p=0.2) with default training=True."""
    x = self.l1(x)
    x = self.embedding(x)
    for layer in self.layers:
        x = layer(x)[0]
    x = torch.max(x, dim=1).values
    x = F.dropout(x, p=0.2)  # Deliberately remains active even while self.training is False.
    x = self.l2(x)
    return x


def forward_eval_aware(self, x: torch.Tensor) -> torch.Tensor:
    """Patched inference path: functional dropout follows the module's train/eval state."""
    x = self.l1(x)
    x = self.embedding(x)
    for layer in self.layers:
        x = layer(x)[0]
    x = torch.max(x, dim=1).values
    x = F.dropout(x, p=0.2, training=self.training)
    x = self.l2(x)
    return x


def set_seed(seed: int, device: torch.device) -> None:
    torch.manual_seed(seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed)


def run_one_pass(model, samples, idx_to_label, seed: int, mode: str, device: torch.device):
    set_seed(seed, device)
    model.eval()
    model.forward = MethodType(
        forward_original_unconditional if mode == "original_unconditional" else forward_eval_aware,
        model,
    )
    results = []
    with torch.inference_mode():
        for sample in samples:
            x = torch.from_numpy(sample["features"]).unsqueeze(0).to(device=device, dtype=torch.float32)
            logits = model(x)
            if tuple(logits.shape) != (1, 50) or not torch.isfinite(logits).all():
                raise RuntimeError(f"Invalid logits for {sample['key']}: shape={tuple(logits.shape)}")
            probs = torch.softmax(logits, dim=1)[0]
            top_values, top_indices = torch.topk(probs, k=5)
            top_indices_list = [int(v) for v in top_indices.detach().cpu().tolist()]
            top_labels = [idx_to_label[v] for v in top_indices_list]
            results.append({
                "sample_key": sample["key"],
                "video_path": sample["video_path"],
                "label": sample["label"],
                "prediction": top_labels[0],
                "correct": bool(top_labels[0] == sample["label"]),
                "confidence": float(top_values[0].item()),
                "top5_predictions": top_labels,
                "top5_correct": bool(sample["label"] in top_labels),
                "seed": seed,
                "mode": mode,
                "probabilities": probs.detach().cpu().numpy().astype(np.float32),
            })
    return results


def accuracy(rows: list[dict], key: str) -> float:
    return sum(bool(r[key]) for r in rows) / len(rows) if rows else math.nan


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def bootstrap_ci(values: list[float], seed: int = 20261107, resamples: int = 10000):
    """Clip-level paired bootstrap; inference-seed variation is already averaged per clip."""
    rng = np.random.default_rng(seed)
    x = np.asarray(values, dtype=np.float64)
    if x.size == 0:
        return {"lower_95": None, "upper_95": None, "resamples": resamples}
    means = np.empty(resamples, dtype=np.float64)
    for i in range(resamples):
        ix = rng.integers(0, x.size, size=x.size)
        means[i] = float(x[ix].mean())
    lo, hi = np.quantile(means, [0.025, 0.975])
    return {"lower_95": float(lo), "upper_95": float(hi), "resamples": resamples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Pinned AI4Bharat INCLUDE repository root")
    parser.add_argument("--checkpoint", required=True, type=Path, help="Official INCLUDE-50 Transformer-small checkpoint")
    parser.add_argument("--feature-cache", required=True, type=Path, help="validation_features.npz created by the uploaded campaign")
    parser.add_argument("--cache-metadata", required=True, type=Path, help="matching feature_cache/cache_metadata.json")
    parser.add_argument("--label-map", type=Path, default=None, help="Defaults to <repo>/label_maps/label_map_include50.json")
    parser.add_argument("--output", type=Path, default=Path("outputs/dropout_ab_retest_20seeds"))
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--seeds", default=",".join(str(101 * i) for i in range(1, 21)),
                        help="Comma-separated matched seeds; default is 101,202,...,2020")
    args = parser.parse_args()

    repo = args.repo.resolve()
    checkpoint_path = args.checkpoint.resolve()
    cache_path = args.feature_cache.resolve()
    metadata_path = args.cache_metadata.resolve()
    label_map_path = args.label_map.resolve() if args.label_map else repo / "label_maps" / "label_map_include50.json"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    if args.threads < 1:
        raise ValueError("--threads must be >= 1")
    seeds = [int(x.strip()) for x in args.seeds.split(",") if x.strip()]
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("Provide at least one unique seed.")

    torch.set_num_threads(args.threads)
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
        if device.type == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available in this Python environment.")

    commit, transformer_sha = verify_repo(repo)
    label_to_idx, idx_to_label = load_labels(label_map_path)
    samples, cache_metadata, cache_sha = validate_cache(cache_path, metadata_path, label_to_idx)
    model = load_model(repo, checkpoint_path, device)

    original_all: dict[int, list[dict]] = {}
    patched_all: dict[int, list[dict]] = {}
    seed_metric_rows = []
    prediction_rows = []

    for seed in seeds:
        print(f"Running seed {seed}: original unconditional dropout", flush=True)
        original = run_one_pass(model, samples, idx_to_label, seed, "original_unconditional", device)
        original_all[seed] = original
        print(f"Running seed {seed}: evaluation-aware dropout", flush=True)
        patched = run_one_pass(model, samples, idx_to_label, seed, "patched_eval_aware", device)
        patched_all[seed] = patched

        for mode, rows in [("original_unconditional", original), ("patched_eval_aware", patched)]:
            seed_metric_rows.append({
                "mode": mode,
                "seed": seed,
                "sample_count": len(rows),
                "top1_accuracy": accuracy(rows, "correct"),
                "top5_accuracy": accuracy(rows, "top5_correct"),
                "mean_confidence": statistics.mean(r["confidence"] for r in rows),
                "confidence_population_std": statistics.pstdev(r["confidence"] for r in rows),
                "accepted_count_at_0_4": sum(r["confidence"] >= 0.4 for r in rows),
                "accepted_coverage_at_0_4": sum(r["confidence"] >= 0.4 for r in rows) / len(rows),
                "model_training_flag": bool(model.training),
            })
            prediction_rows.extend({k: v for k, v in r.items() if k != "probabilities"} for r in rows)

    per_sample_rows = []
    paired_accuracy_deltas = []
    patched_max_probability_delta = 0.0
    patched_top1_identical = True
    patched_confidence_identical = True

    for i, sample in enumerate(samples):
        orig = [original_all[s][i] for s in seeds]
        patt = [patched_all[s][i] for s in seeds]
        orig_predictions = [r["prediction"] for r in orig]
        patch_predictions = [r["prediction"] for r in patt]
        orig_counts = Counter(orig_predictions)
        orig_majority = orig_counts.most_common(1)[0][0]
        patch_counts = Counter(patch_predictions)
        patch_majority = patch_counts.most_common(1)[0][0]
        orig_correct_rate = sum(r["correct"] for r in orig) / len(orig)
        patch_correct = bool(patt[0]["correct"])
        paired_accuracy_deltas.append(float(patch_correct) - orig_correct_rate)

        patch_probability_reference = patt[0]["probabilities"]
        for item in patt[1:]:
            max_delta = float(np.max(np.abs(item["probabilities"] - patch_probability_reference)))
            patched_max_probability_delta = max(patched_max_probability_delta, max_delta)
            if item["prediction"] != patt[0]["prediction"]:
                patched_top1_identical = False
            if abs(item["confidence"] - patt[0]["confidence"]) > 1e-7:
                patched_confidence_identical = False

        per_sample_rows.append({
            "sample_key": sample["key"],
            "video_path": sample["video_path"],
            "label": sample["label"],
            "original_top1_predictions_by_seed": "|".join(orig_predictions),
            "original_unique_top1_count": len(orig_counts),
            "original_majority_prediction": orig_majority,
            "original_majority_votes": orig_counts[orig_majority],
            "original_top1_correct_rate": orig_correct_rate,
            "original_top5_hit_rate": sum(r["top5_correct"] for r in orig) / len(orig),
            "original_confidence_mean": statistics.mean(r["confidence"] for r in orig),
            "original_confidence_population_std": statistics.pstdev(r["confidence"] for r in orig),
            "patched_top1_predictions_by_seed": "|".join(patch_predictions),
            "patched_prediction": patch_majority,
            "patched_correct": patch_correct,
            "patched_top5_correct": bool(patt[0]["top5_correct"]),
            "patched_confidence": float(patt[0]["confidence"]),
            "patched_matches_original_majority": patch_majority == orig_majority,
        })

    original_run_metrics = [r for r in seed_metric_rows if r["mode"] == "original_unconditional"]
    patched_run_metrics = [r for r in seed_metric_rows if r["mode"] == "patched_eval_aware"]
    orig_majority_accuracy = sum(r["original_majority_prediction"] == r["label"] for r in per_sample_rows) / len(per_sample_rows)
    patched_accuracy = accuracy(per_sample_rows, "patched_correct")
    patched_changed = sum(not r["patched_matches_original_majority"] for r in per_sample_rows)
    improves = sum(r["patched_correct"] and r["original_majority_prediction"] != r["label"] for r in per_sample_rows)
    worsens = sum((not r["patched_correct"]) and r["original_majority_prediction"] == r["label"] for r in per_sample_rows)

    summary = {
        "complete": True,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "split": "official INCLUDE-50 validation only",
        "sample_count": len(samples),
        "repeats_per_mode": len(seeds),
        "seeds": seeds,
        "device": str(device),
        "torch_version": torch.__version__,
        "repo_commit": commit,
        "transformer_source_sha256_at_run": transformer_sha,
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "feature_cache_sha256": cache_sha,
        "official_validation_path_list_sha256": OFFICIAL_VAL_LIST_SHA256,
        "feature_contract": [169, 134],
        "feature_cache_extraction_failures": cache_metadata.get("failed_count"),
        "dropout_modes": {
            "original_unconditional": "F.dropout(x, p=0.2), default training=True even when model.eval()",
            "patched_eval_aware": "F.dropout(x, p=0.2, training=self.training); dropout disabled in model.eval()",
        },
        "original_top1_accuracy_mean_across_seeds": statistics.mean(r["top1_accuracy"] for r in original_run_metrics),
        "original_top1_accuracy_population_std_across_seeds": statistics.pstdev(r["top1_accuracy"] for r in original_run_metrics),
        "original_top5_accuracy_mean_across_seeds": statistics.mean(r["top5_accuracy"] for r in original_run_metrics),
        "patched_top1_accuracy_mean_across_seeds": statistics.mean(r["top1_accuracy"] for r in patched_run_metrics),
        "patched_top1_accuracy_population_std_across_seeds": statistics.pstdev(r["top1_accuracy"] for r in patched_run_metrics),
        "patched_top5_accuracy_mean_across_seeds": statistics.mean(r["top5_accuracy"] for r in patched_run_metrics),
        "original_majority_vote_top1_accuracy": orig_majority_accuracy,
        "patched_top1_accuracy": patched_accuracy,
        "patched_correct_count": sum(bool(r["patched_correct"]) for r in per_sample_rows),
        "original_clips_with_prediction_variation": sum(r["original_unique_top1_count"] > 1 for r in per_sample_rows),
        "patched_predictions_identical_across_seeds": patched_top1_identical,
        "patched_confidences_identical_within_1e_7": patched_confidence_identical,
        "patched_max_absolute_probability_delta_across_seeds": patched_max_probability_delta,
        "patched_vs_original_majority": {
            "changed_predictions": patched_changed,
            "corrected_errors": improves,
            "new_errors": worsens,
            "net_correct_clip_difference": improves - worsens,
        },
        "paired_clip_bootstrap_for_patch_correctness_minus_original_mean_correctness": {
            "mean_difference": statistics.mean(paired_accuracy_deltas),
            "unit": "validation clip; original correctness averaged over stochastic repetitions",
            "confidence_interval": bootstrap_ci(paired_accuracy_deltas),
            "interpretation_note": "This resamples validation clips; it is not a confidence interval for new signers or an independent test population.",
        },
        "interpretation_guardrail": (
            "This experiment isolates final-layer functional-dropout inference behaviour on one fixed set of cached "
            "official validation feature tensors. It does not evaluate browser capture, video preprocessing, webcam "
            "generalization, model retraining, or the held-out test split."
        ),
    }

    write_csv(output / "seed_metrics.csv", seed_metric_rows, list(seed_metric_rows[0].keys()))
    write_csv(output / "predictions_long.csv", prediction_rows, list(prediction_rows[0].keys()))
    write_csv(output / "per_sample_summary.csv", per_sample_rows, list(per_sample_rows[0].keys()))
    write_json(output / "summary.json", summary)
    write_json(output / "run_environment.json", {
        "python": sys.version,
        "torch": torch.__version__,
        "numpy": np.__version__,
        "device": str(device),
        "repo": str(repo),
        "checkpoint": str(checkpoint_path),
        "feature_cache": str(cache_path),
        "cache_metadata": str(metadata_path),
        "label_map": str(label_map_path),
        "transformer_source_sha256": transformer_sha,
        "feature_cache_sha256": cache_sha,
        "checkpoint_sha256": sha256_file(checkpoint_path),
    })

    print("\nCompleted dropout A/B study.")
    print(f"Original mean Top-1: {summary['original_top1_accuracy_mean_across_seeds']:.4%}")
    print(f"Patched mean Top-1:  {summary['patched_top1_accuracy_mean_across_seeds']:.4%}")
    print(f"Original mean Top-5: {summary['original_top5_accuracy_mean_across_seeds']:.4%}")
    print(f"Patched mean Top-5:  {summary['patched_top5_accuracy_mean_across_seeds']:.4%}")
    print(f"Original clips with prediction variation: {summary['original_clips_with_prediction_variation']}/{len(samples)}")
    print(f"Patched predictions stable across seeds: {patched_top1_identical}")
    print(f"Outputs: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
