#!/usr/bin/env python3
"""Create the exact 77-video INCLUDE-50 validation manifest from the pinned upstream path list.

This does not download video data and does not use broader metadata label matches. It
fails closed if the repository commit, path-list hash, entry count, or class labels differ.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.labels import load_label_map
from prepare_include_references import canonical_model_label

PINNED_REPO_COMMIT = "df348f4a640060d4d39f02556d9f96851e8c46c7"
OFFICIAL_VAL_LIST_SHA256 = "044f56003b3af73e13bb889386cd9a424b25d74a172abcad2848fad2f1679541"
EXPECTED_VAL_COUNT = 77
VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".m4v", ".webm"}


def verify_repo_commit(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ValueError(
            f"Could not verify the AI4Bharat Git checkout at {repo}. "
            "Use the complete repository clone with its .git directory. "
            f"Details: {detail.strip()}"
        ) from exc
    commit = result.stdout.strip().lower()
    if commit != PINNED_REPO_COMMIT:
        raise ValueError(
            f"AI4Bharat repository HEAD is {commit}, expected pinned commit {PINNED_REPO_COMMIT}. "
            "Do not generate a research manifest from a different split revision."
        )
    return commit


def parse_validation_list(raw: bytes, valid_labels: set[str]) -> list[dict[str, str]]:
    digest = hashlib.sha256(raw).hexdigest()
    if digest != OFFICIAL_VAL_LIST_SHA256:
        raise ValueError(
            f"include50_val.txt SHA256 mismatch: {digest}; expected {OFFICIAL_VAL_LIST_SHA256}."
        )
    text = raw.decode("utf-8-sig")
    unique: set[str] = set()
    rows: list[dict[str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        relative = line.strip().replace("\\", "/")
        if not relative or relative.startswith("#"):
            continue
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Unsafe or unsupported video path on line {line_no}: {relative!r}")
        key = str(path).casefold()
        if key in unique:
            raise ValueError(f"Duplicate video path in official validation list: {relative}")
        unique.add(key)
        parent_label = path.parent.name
        model_label = canonical_model_label(parent_label, valid_labels)
        if model_label is None:
            raise ValueError(f"Could not map validation directory {parent_label!r} to INCLUDE-50 on line {line_no}.")
        rows.append({
            "video_path": str(path),
            "label": model_label,
            "split": "val",
            "parent_label": parent_label,
            "source_split_file": "train_test_paths/include50_val.txt",
            "source_sha256": digest,
        })
    if len(rows) != EXPECTED_VAL_COUNT:
        raise ValueError(f"Expected {EXPECTED_VAL_COUNT} official validation entries, found {len(rows)}.")
    return rows


def load_official_validation_rows(repo: Path, valid_labels: set[str]) -> tuple[list[dict[str, str]], str, str]:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"AI4Bharat repository not found: {repo}")
    commit = verify_repo_commit(repo)
    split_path = repo / "train_test_paths" / "include50_val.txt"
    if not split_path.is_file():
        raise FileNotFoundError(f"Official INCLUDE-50 validation list not found: {split_path}")
    raw = split_path.read_bytes()
    rows = parse_validation_list(raw, valid_labels)
    return rows, commit, hashlib.sha256(raw).hexdigest()


def write_manifest(rows: list[dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["video_path", "label", "split", "parent_label", "source_split_file", "source_sha256"]
    temp = output.with_name(output.name + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(os.getenv("AI4BHARAT_REPO", "")) if os.getenv("AI4BHARAT_REPO") else None,
                        help="Pinned AI4Bharat INCLUDE clone (defaults to AI4BHARAT_REPO)")
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "include50_official_val_manifest.csv")
    args = parser.parse_args()
    try:
        repo = args.repo
        if repo is None:
            settings = Settings()
            repo = settings.ai4bharat_repo
        if repo is None:
            raise ValueError("Provide --repo or set AI4BHARAT_REPO.")
        labels = load_label_map(args.label_map)
        rows, commit, digest = load_official_validation_rows(repo, set(labels))
        output = args.output.expanduser().resolve()
        write_manifest(rows, output)
        represented = len({row["label"] for row in rows})
        print(f"Manifest created: {output}")
        print(f"Official validation entries: {len(rows)} | represented classes: {represented}/50")
        print(f"Repository commit: {commit}")
        print(f"Validation path-list SHA256: {digest}")
        print("The manifest lists all 77 expected validation items even when the corresponding local video is not downloaded.")
        return 0
    except Exception as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
