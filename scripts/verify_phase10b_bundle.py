#!/usr/bin/env python3
"""Verify Phase 10B result ZIP structure, run metadata and SHA-256 entries."""
from __future__ import annotations
import hashlib
import json
import sys
import zipfile
from pathlib import PurePosixPath, Path

REQUIRED = {
    "results/phase10b_summary.json",
    "results/PHASE10B_BROWSER_LIFECYCLE_REPORT.md",
    "results/phase10b_mode_metrics.csv",
    "results/phase10b_clip_metrics.csv",
    "results/phase10b_frame_metrics.csv",
    "results/phase10b_event_timeline.csv",
    "results/phase10b_playback_heartbeats.csv",
    "results/phase10b_hidden_intervals.csv",
    "results/phase10b_visible_unfocused_intervals.csv",
    "results/phase10b_source_pts_metadata.csv",
    "logs/pytest_full_suite.log",
    "metadata/run_environment.txt",
    "metadata/pytest_exit_code.txt",
    "SHA256SUMS.txt",
}
MODES = ("foreground_focused", "visible_unfocused", "hidden_background", "focus_recovery")


def verify(bundle: Path) -> int:
    with zipfile.ZipFile(bundle) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError("ZIP CRC failure: " + bad)
        names = set(archive.namelist())
        missing = REQUIRED - names
        if missing:
            raise RuntimeError("Missing required bundle entries: " + ", ".join(sorted(missing)))
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Unsafe ZIP path: " + name)

        checksum_lines = archive.read("SHA256SUMS.txt").decode("utf-8").splitlines()
        checked_names: set[str] = set()
        for line in checksum_lines:
            if not line.strip():
                continue
            expected, rel = line.split("  ", 1)
            if rel in checked_names:
                raise RuntimeError("Duplicate checksum entry: " + rel)
            checked_names.add(rel)
            if rel not in names:
                raise RuntimeError("Checksum entry missing from ZIP: " + rel)
            actual = hashlib.sha256(archive.read(rel)).hexdigest()
            if actual.lower() != expected.lower():
                raise RuntimeError("SHA-256 mismatch: " + rel)
        if checked_names != names - {"SHA256SUMS.txt"}:
            missing_hashes = sorted((names - {"SHA256SUMS.txt"}) - checked_names)
            raise RuntimeError("Files missing from SHA256SUMS.txt: " + ", ".join(missing_hashes))

        summary = json.loads(archive.read("results/phase10b_summary.json").decode("utf-8"))
        if summary.get("status") != "completed" or summary.get("mode_count") != 4 or summary.get("clip_count") != 24:
            raise RuntimeError("Phase 10B summary does not describe 4 modes and 24 accepted clips")
        if summary.get("iot_enabled") is not False or summary.get("classifier_checkpoint_loaded") is not False:
            raise RuntimeError("Safety metadata invalid: IoT must be disabled and classifier/checkpoint not loaded")
        if int(archive.read("metadata/pytest_exit_code.txt").decode("utf-8").strip()) != 0:
            raise RuntimeError("Full project test suite did not exit successfully")
        environment = archive.read("metadata/run_environment.txt").decode("utf-8").lower()
        if "iot_enabled=false" not in environment or "classifier_checkpoint_loaded=false" not in environment:
            raise RuntimeError("Run environment safety metadata is missing or invalid")

        indexes = [name for name in names if name.startswith("captures/") and name.endswith("/capture_index.json")]
        containers = [name for name in names if name.startswith("captures/") and name.endswith(".islbrw")]
        if len(indexes) != 4 or len(containers) != 24:
            raise RuntimeError(f"Expected 4 capture indexes/24 containers; found {len(indexes)}/{len(containers)}")
        found_modes = set()
        for mode in MODES:
            index_name = f"captures/{mode}/capture_index.json"
            if index_name not in names:
                raise RuntimeError("Capture index missing for mode: " + mode)
            index = json.loads(archive.read(index_name).decode("utf-8"))
            config = index.get("capture_config", {})
            if config.get("phase10b_mode") != mode or config.get("phase10b_run_id") != mode:
                raise RuntimeError("Capture index identity mismatch for mode: " + mode)
            if index.get("official_split_count") != 77 or index.get("received_count") != 6:
                raise RuntimeError("Capture index count mismatch for mode: " + mode)
            received = index.get("received", [])
            if len(received) != 6:
                raise RuntimeError("Capture index does not list six clips for mode: " + mode)
            for item in received:
                rel = item.get("container", "")
                container_name = f"captures/{mode}/{rel.replace(chr(92), '/') }"
                if container_name not in names:
                    raise RuntimeError("Capture container missing: " + container_name)
                if hashlib.sha256(archive.read(container_name)).hexdigest() != item.get("sha256"):
                    raise RuntimeError("Capture-container hash mismatch: " + container_name)
            found_modes.add(mode)
        if found_modes != set(MODES):
            raise RuntimeError("Not all four lifecycle modes are represented")
    print(f"ZIP_TEST=OK | INTERNAL_SHA256={len(checked_names)}/{len(checked_names)} | INDEXES=4/4 | CLIPS=24/24")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_phase10b_bundle.py RESULT.zip")
        return 2
    return verify(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PHASE10B_BUNDLE_INVALID: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
