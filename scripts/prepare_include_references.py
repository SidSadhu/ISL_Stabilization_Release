#!/usr/bin/env python3
"""Fetch split-aware INCLUDE metadata with retries, checkpointing and provenance.

This downloads metadata only. It does not download video files.

A row is retained when either:
  * the dataset's include_50 field is truthy, or
  * its official gloss maps to one of the project's 50 classifier labels.

The two provenance facts are stored separately; label matching never rewrites the
source include_50 flag.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://datasets-server.huggingface.co/rows"
DATASET = "ai4bharat/INCLUDE"
SPLITS = ("train", "val", "test")
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".m4v", ".webm"}
ALIASES = {
    "biglarge": {"biglarge", "big large", "large"},
    "cellphone": {"cellphone", "cell phone", "mobile phone"},
    "goodmorning": {"goodmorning", "good morning"},
    "smalllittle": {"smalllittle", "small little", "small", "little"},
    "storeorshop": {"storeorshop", "store or shop", "store", "shop"},
    "trainticket": {"trainticket", "train ticket"},
    "tshirt": {"tshirt", "t shirt", "t-shirt"},
    "youplural": {"youplural", "you plural", "you (plural)"},
    "thankyou": {"thankyou", "thank you"},
    "i": {"i", "me"},
}


def is_true_like(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def normalize_text(value: str) -> str:
    return "".join(ch.lower() for ch in str(value) if ch.isalnum())


def canonical_model_label(raw_label: str, valid_labels: set[str]) -> str | None:
    leaf = str(raw_label).replace("\\", "/").split("/")[-1]
    leaf = re.sub(r"^\s*\d+\s*[.\-_:]?\s*", "", leaf).strip()
    normalized = normalize_text(leaf)
    for label in valid_labels:
        options = {normalize_text(label)}
        options.update(normalize_text(alias) for alias in ALIASES.get(label, set()))
        if normalized in options:
            return label
    return None


def normalize_source_row(raw: dict[str, Any], split: str, valid_labels: set[str]) -> dict[str, Any] | None:
    label = str(raw.get("label", ""))
    path = str(raw.get("video_path", "")).replace("\\", "/").lstrip("/")
    if not path or ".." in Path(path).parts or Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    model_label = canonical_model_label(label, valid_labels)
    source_include_50 = is_true_like(raw.get("include_50", False))
    model_class_match = model_label is not None
    if not source_include_50 and not model_class_match:
        return None
    if split == "validation":
        split = "val"
    if split not in {"train", "val", "test"}:
        return None
    return {
        "parent_label": str(raw.get("parent_label", "")),
        "label": label,
        "video_path": path,
        "include_50": source_include_50,
        "model_class_match": model_class_match,
        "matched_model_label": model_label or "",
        "split": split,
    }


def fetch_page(split: str, offset: int, length: int = 100, *, opener=urlopen,
               sleep=time.sleep, retries: int = 6) -> dict[str, Any]:
    query = urlencode({"dataset": DATASET, "config": "default", "split": split,
                       "offset": offset, "length": length})
    request = Request(
        f"{API}?{query}",
        headers={"User-Agent": "ISL-INCLUDE50-ReferenceGallery/1.1", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with opener(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_STATUS or attempt + 1 >= retries:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait = float(retry_after) if retry_after else min(30.0, 1.5 * (2 ** attempt))
            except (TypeError, ValueError):
                wait = min(30.0, 1.5 * (2 ** attempt))
            sleep(wait + random.uniform(0, 0.25))
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise
            sleep(min(30.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.25))
    assert last_error is not None
    raise last_error


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/include50_video_metadata.json"))
    parser.add_argument("--label-map", type=Path, default=Path("config/label_map_include50.json"))
    parser.add_argument("--page-size", type=int, default=100, choices=(50, 100))
    parser.add_argument("--restart", action="store_true", help="Discard progress checkpoint and start all splits again")
    args = parser.parse_args()

    try:
        label_map = json.loads(args.label_map.read_text(encoding="utf-8"))
        valid_labels = set(label_map.keys())
        if len(valid_labels) != 50:
            raise ValueError(f"Expected 50 labels in {args.label_map}, found {len(valid_labels)}")
        output = args.output.expanduser().resolve()
        checkpoint_path = Path(str(output) + ".progress.json")
        if args.restart and checkpoint_path.exists():
            checkpoint_path.unlink()
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("dataset") != DATASET:
                raise ValueError("Progress checkpoint belongs to a different dataset; move it aside or use --restart.")
            print(f"Resuming metadata fetch from checkpoint: {checkpoint_path}")
        else:
            checkpoint = {
                "dataset": DATASET,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "page_size": args.page_size,
                "splits": {split: {"offset": 0, "total": None, "complete": False} for split in SPLITS},
                "videos": {},
            }
            atomic_write_json(checkpoint_path, checkpoint)

        for split in SPLITS:
            state = checkpoint["splits"][split]
            offset = int(state.get("offset", 0))
            total = state.get("total")
            if state.get("complete"):
                print(f"{split}: already complete ({offset}/{total})")
                continue
            while total is None or offset < int(total):
                try:
                    page = fetch_page(split, offset, args.page_size)
                except Exception as exc:
                    # Page checkpoint is already durable; a later run resumes from this offset.
                    atomic_write_json(checkpoint_path, checkpoint)
                    print(f"ERROR: Could not fetch {split} metadata at offset {offset} after retries: {exc}", file=sys.stderr)
                    print(f"Progress saved to {checkpoint_path}. Re-run the same command to resume; do not use --restart.", file=sys.stderr)
                    return 2
                page_rows = page.get("rows", [])
                total = int(page.get("num_rows_total", len(page_rows)))
                state["total"] = total
                if not page_rows:
                    state["offset"] = offset
                    state["complete"] = True
                    break
                for wrapped in page_rows:
                    raw = wrapped.get("row", wrapped)
                    if not isinstance(raw, dict):
                        continue
                    normalized = normalize_source_row(raw, split, valid_labels)
                    if normalized:
                        key = f"{split}|{normalized['video_path']}|{normalized['label']}".casefold()
                        checkpoint["videos"][key] = normalized
                offset += len(page_rows)
                state["offset"] = offset
                state["total"] = total
                state["complete"] = offset >= total
                atomic_write_json(checkpoint_path, checkpoint)
                flagged = sum(1 for r in checkpoint["videos"].values() if r["include_50"])
                matched = sum(1 for r in checkpoint["videos"].values() if r["model_class_match"])
                print(f"Fetched {min(offset, total)}/{total} rows from {split}; source INCLUDE-50 flagged: {flagged}; model-class matches: {matched}; indexed: {len(checkpoint['videos'])}")
                if offset < total:
                    time.sleep(0.15)
            state["complete"] = True
            atomic_write_json(checkpoint_path, checkpoint)

        all_rows = list(checkpoint["videos"].values())
        all_rows.sort(key=lambda r: (SPLITS.index(r["split"]), r["matched_model_label"], r["video_path"].casefold()))
        source_count = sum(1 for r in all_rows if r["include_50"])
        class_match_count = sum(1 for r in all_rows if r["model_class_match"])
        payload = {
            "dataset": DATASET,
            "metadata_source": "https://huggingface.co/datasets/ai4bharat/INCLUDE",
            "metadata_api": API,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "selection_policy": "source include_50 truthy OR label maps to one of the 50 model classes; provenance retained separately",
            "source_include_50_video_count": source_count,
            "model_class_match_video_count": class_match_count,
            "video_count": len(all_rows),
            "videos": all_rows,
        }
        atomic_write_json(output, payload)
        checkpoint_path.unlink(missing_ok=True)
        print(f"\nSaved {len(all_rows)} metadata entries to: {output}")
        print(f"Source INCLUDE-50 flags: {source_count}; model-class matches: {class_match_count}")
        print("No video files were downloaded by this script.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
