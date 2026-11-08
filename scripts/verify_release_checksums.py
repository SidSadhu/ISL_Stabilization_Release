#!/usr/bin/env python3
"""Verify the clean release tree against SHA256SUMS.txt (excluding dynamic outputs)."""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "SHA256SUMS.txt"
EXCLUDED_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__", "outputs", "var"}
EXCLUDED_FILES = {MANIFEST_NAME, ".env"}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_files(root: Path) -> set[str]:
    result: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.name in EXCLUDED_FILES or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.name.startswith(".env.") and path.name != ".env.example":
            continue
        result.add(relative.as_posix())
    return result


def verify(root: Path, manifest_path: Path) -> list[str]:
    expected: dict[str, str] = {}
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if not match:
            raise ValueError(f"Invalid checksum line {line_no}")
        digest, raw_path = match.groups()
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts or raw_path in expected:
            raise ValueError(f"Unsafe or duplicate manifest path on line {line_no}: {raw_path}")
        target = (root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"Checksum path escapes project root: {raw_path}") from exc
        expected[raw_path] = digest.lower()
        if not target.is_file():
            raise ValueError(f"Manifest file is missing: {raw_path}")
        actual = digest_file(target)
        if actual != digest.lower():
            raise ValueError(f"SHA256 mismatch: {raw_path} expected={digest.lower()} actual={actual}")
    actual_files = package_files(root)
    missing = sorted(actual_files - set(expected))
    stale = sorted(set(expected) - actual_files)
    if missing or stale:
        raise ValueError(f"Manifest coverage mismatch; unlisted={missing[:10]} stale={stale[:10]}")
    return sorted(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=ROOT / MANIFEST_NAME)
    args = parser.parse_args()
    try:
        files = verify(args.root.resolve(), args.manifest.resolve())
        print(f"Release checksums verified: {len(files)} files")
        return 0
    except Exception as exc:
        print(f"CHECKSUM FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
