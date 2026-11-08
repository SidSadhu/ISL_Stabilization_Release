#!/usr/bin/env python3
"""Verify Phase 10 result ZIP integrity and every SHA256SUMS.txt entry."""
from __future__ import annotations
import hashlib
import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: verify_phase10_bundle.py <bundle.zip>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC failure: {bad}")
        names = set(archive.namelist())
        sums_name = "SHA256SUMS.txt"
        if sums_name not in names:
            raise RuntimeError("SHA256SUMS.txt is missing")
        text = archive.read(sums_name).decode("utf-8")
        checked = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            expected, relative = line.split("  ", 1)
            if relative not in names:
                raise RuntimeError(f"Checksum entry missing from ZIP: {relative}")
            actual = hashlib.sha256(archive.read(relative)).hexdigest()
            if actual.lower() != expected.lower():
                raise RuntimeError(f"SHA-256 mismatch: {relative}")
            checked += 1
        if checked < 1:
            raise RuntimeError("SHA256SUMS.txt contains no entries")
        required = {
            "results/phase10_summary.json",
            "results/PHASE10_BROWSER_LIFECYCLE_REPORT.md",
            "metadata/run_environment.txt",
            "logs/pytest_full_suite.log",
        }
        missing = required - names
        if missing:
            raise RuntimeError(f"Required bundle files missing: {sorted(missing)}")
        for mode in ("foreground_focused", "visible_unfocused", "hidden_background", "focus_recovery"):
            index = f"captures/{mode}/capture_index.json"
            if index not in names:
                raise RuntimeError(f"Capture index missing for mode {mode}")
            clip_files = [n for n in names if n.startswith(f"captures/{mode}/clips/") and n.endswith(".islbrw")]
            if len(clip_files) != 6:
                raise RuntimeError(f"Mode {mode} has {len(clip_files)} capture containers; expected 6")
    print(f"ZIP_TEST=OK | INTERNAL_SHA256={checked}/{checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
