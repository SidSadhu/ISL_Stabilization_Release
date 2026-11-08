#!/usr/bin/env python3
"""Run the Phase 9 capture repeatability workflow without complex PowerShell parsing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import normalize_video_path  # noqa: E402
from serve_browser_validation_capture import read_and_verify_manifest  # noqa: E402

TARGET_PATHS = (
    "Greetings/51. Good Morning/MVI_0099.MOV",
    "Colours/55. White/MVI_5063.MOV",
    "Adjectives/83. big large/MVI_9396.MOV",
    "Adjectives/79. short/MVI_9302.MOV",
    "Means_of_Transportation/11. Car/MVI_3118.MOV",
    "Pronouns/40. I/MVI_9922.MOV",
)
PASS_SPECS = (
    ("sync", 1, 8791),
    ("async", 1, 8792),
    ("async", 2, 8793),
    ("sync", 2, 8794),
    ("sync", 3, 8795),
    ("async", 3, 8796),
)
REQUIRED_OUTPUTS = (
    "phase9_summary.json",
    "PHASE9_REPEATABILITY_REPORT.md",
    "phase9_clip_repeat_metrics.csv",
    "phase9_frame_metrics.csv",
    "phase9_repeatability.csv",
    "phase9_encoder_summary.csv",
    "phase9_encoder_comparison.csv",
    "phase9_event_timeline.csv",
    "phase9_playback_heartbeats.csv",
    "phase9_source_pts_metadata.csv",
)


def build_passes() -> tuple[tuple[str, int, int, str], ...]:
    return tuple((encoder, repeat, port, f"{encoder}_r{repeat}") for encoder, repeat, port in PASS_SPECS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_safety(project_root: Path) -> None:
    if _truthy(os.environ.get("ISL_IOT_ENABLED")):
        raise RuntimeError("Process environment ISL_IOT_ENABLED is true. Phase 9 is blocked.")
    env_file = project_root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$", line, flags=re.IGNORECASE):
                raise RuntimeError(".env contains ISL_IOT_ENABLED=true. Phase 9 will not edit it.")
    map_path = project_root / "config" / "iot_label_map.json"
    config = json.loads(map_path.read_text(encoding="utf-8"))
    mappings = config.get("mappings", {})
    if not isinstance(mappings, dict) or mappings:
        raise RuntimeError("IoT mapping is not empty. Phase 9 requires zero mappings.")
    settings = (project_root / "src" / "isl_recognition" / "app_config.py").read_text(encoding="utf-8")
    if not re.search(r"pre_iot_lockdown\s*:\s*bool\s*=\s*True", settings):
        raise RuntimeError("Hard pre-IoT lockdown not found.")
    os.environ["ISL_IOT_ENABLED"] = "false"


def run_logged(command: list[str], cwd: Path, log_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", check=False)
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines[-30:]:
        print(line, flush=True)
    return result


def http_json(url: str, timeout: float = 4.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=5)
        else:
            process.terminate()
            process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass


@dataclass
class PassResult:
    encoder: str
    repeat: int
    run_id: str
    port: int
    output_dir: Path


def capture_pass(
    *, python: Path, project_root: Path, repo: Path, manifest: Path, output_dir: Path,
    encoder: str, repeat: int, run_id: str, port: int, log_root: Path, target_paths: set[str],
) -> PassResult:
    output_dir.mkdir(parents=True, exist_ok=False)
    stdout_path = log_root / f"{run_id}_server.stdout.log"
    stderr_path = log_root / f"{run_id}_server.stderr.log"
    command = [
        str(python), "-u", str(SCRIPTS / "serve_phase9_capture.py"),
        "--manifest", str(manifest), "--repo", str(repo), "--output", str(output_dir),
        "--encoder", encoder, "--repeat", str(repeat), "--run-id", run_id,
        "--port", str(port), "--max-upload-mb", "512", "--open-browser",
    ]
    env = os.environ.copy()
    env["ISL_IOT_ENABLED"] = "false"
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        process = subprocess.Popen(command, cwd=project_root, env=env, stdout=out, stderr=err,
                                   text=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            ready = False
            base_url = f"http://127.0.0.1:{port}"
            for _ in range(60):
                if process.poll() is not None:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(f"Phase 9 {run_id} receiver exited before ready. {tail}")
                try:
                    status = http_json(base_url + "/api/status", timeout=2.5)
                    if status.get("expected_count") == 77:
                        ready = True
                        break
                except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(1)
            if not ready:
                raise RuntimeError(f"Phase 9 {run_id} receiver did not become ready. See {stdout_path} and {stderr_path}")

            print("", flush=True)
            print(f"BROWSER ACTION REQUIRED - {run_id} ({encoder} encoder, repeat {repeat}/3)", flush=True)
            print("1. Select C:\\AIAssets\\INCLUDE_videos in the opened browser tab.", flush=True)
            print("2. Confirm all six diagnostic clips are matched.", flush=True)
            print(f"3. Click Capture six clips — {encoder} encoder, repeat {repeat}.", flush=True)
            print("Keep this PowerShell window open until this pass reaches 6/6.", flush=True)
            print(f"Phase 9 {run_id} captures received: 0 / 6", flush=True)
            last_count = 0
            deadline = time.monotonic() + 20 * 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(f"Phase 9 {run_id} receiver exited during capture. {tail}")
                status = http_json(base_url + "/api/status", timeout=5)
                received = status.get("received", [])
                paths = {
                    normalize_video_path(str(item.get("video_path", ""))).casefold()
                    for item in received if isinstance(item, dict)
                }
                count = len(paths & target_paths)
                if count != last_count:
                    print(f"Phase 9 {run_id} captures received: {count} / 6", flush=True)
                    last_count = count
                if count == 6:
                    # Allow the final request handler to persist the index and close its response.
                    time.sleep(0.5)
                    break
                time.sleep(2)
            if last_count != 6:
                raise TimeoutError(f"Timed out waiting for Phase 9 {run_id}: received {last_count}/6. Data remains at {output_dir}")
        finally:
            stop_process(process)

    index_path = output_dir / "capture_index.json"
    if not index_path.is_file():
        raise RuntimeError(f"Phase 9 {run_id} capture index missing: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    clip_files = list((output_dir / "clips").glob("*.islbrw"))
    if index.get("received_count") != 6 or len(clip_files) != 6:
        raise RuntimeError(f"Phase 9 {run_id}: expected six containers; index={index.get('received_count')}, files={len(clip_files)}")
    cfg = index.get("capture_config", {})
    if (cfg.get("phase9_encoder"), cfg.get("phase9_repeat"), cfg.get("phase9_run_id")) != (encoder, repeat, run_id):
        raise RuntimeError(f"Phase 9 {run_id}: saved index identity mismatch")
    return PassResult(encoder, repeat, run_id, port, output_dir)


def create_bundle(stage: Path, bundle: Path, python: Path, project_root: Path) -> str:
    if not stage.is_dir():
        raise FileNotFoundError(f"Phase 9 staging directory missing: {stage}")
    hash_lines: list[str] = []
    files_to_hash = [p for p in stage.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"]
    for file in sorted(files_to_hash):
        relative = file.relative_to(stage).as_posix()
        hash_lines.append(f"{sha256_file(file)}  {relative}")
    (stage / "SHA256SUMS.txt").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")
    if bundle.exists():
        bundle.unlink()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(p for p in stage.rglob("*") if p.is_file()):
            archive.write(file, file.relative_to(stage).as_posix())
    verifier = project_root / "scripts" / "verify_phase9_bundle.py"
    result = subprocess.run([str(python), str(verifier), str(bundle)], cwd=project_root,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print(result.stdout.strip(), flush=True)
    if result.returncode != 0:
        raise RuntimeError(f"Phase 9 bundle verification failed: {result.stderr[-2000:]}")
    return sha256_file(bundle)


def run(args: argparse.Namespace) -> int:
    project_root = ROOT.resolve()
    python = Path(sys.executable).resolve()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Expected Python 3.12; found {sys.version_info.major}.{sys.version_info.minor}")
    repo = args.repo.expanduser().resolve()
    data_root = args.data_root.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve()
    ffprobe = args.ffprobe.expanduser().resolve()
    for path, label in ((repo, "AI4Bharat repository"), (data_root, "INCLUDE source videos"), (manifest, "official validation manifest"), (ffprobe, "FFprobe executable")):
        if not path.exists():
            raise FileNotFoundError(f"{label} missing: {path}")
    if not (project_root / "scripts" / "serve_phase9_capture.py").is_file():
        raise FileNotFoundError("Phase 9 capture receiver is missing")
    if not (project_root / "scripts" / "phase9_repeatability_analysis.py").is_file():
        raise FileNotFoundError("Phase 9 analyzer is missing")
    validate_safety(project_root)

    ffprobe_preflight = subprocess.run([str(python), str(SCRIPTS / "phase9_ffprobe_preflight.py"), str(ffprobe)],
                                       cwd=project_root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if ffprobe_preflight.returncode != 0:
        raise RuntimeError(f"FFprobe preflight failed: {(ffprobe_preflight.stdout + ffprobe_preflight.stderr)[-2000:]}")
    ffprobe_version = ffprobe_preflight.stdout.strip().splitlines()[-1]
    print(ffprobe_version, flush=True)

    rows = read_and_verify_manifest(manifest, repo, project_root / "config" / "label_map_include50.json")
    if len(rows) != 77:
        raise RuntimeError(f"Pinned manifest must contain 77 rows, found {len(rows)}")
    rows_by_path = {normalize_video_path(row["video_path"]).casefold(): row for row in rows}
    target_paths = {normalize_video_path(path).casefold() for path in TARGET_PATHS}
    missing_targets = sorted(target_paths - set(rows_by_path))
    if missing_targets:
        raise RuntimeError(f"Phase 9 targets missing from official validation manifest: {missing_targets}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = project_root / "outputs" / f"phase9_repeatability_{stamp}"
    log_root = project_root / "outputs" / f"phase9_run_logs_{stamp}"
    stage = project_root / "outputs" / f"PHASE9_BUNDLE_STAGE_{stamp}"
    bundle = project_root / "outputs" / f"PHASE9_RESULT_BUNDLE_{stamp}.zip"
    output_path.mkdir(parents=True, exist_ok=False)
    log_root.mkdir(parents=True, exist_ok=False)

    runner_meta = log_root / "run_environment.txt"
    test_log = log_root / "pytest_full_suite.log"
    metadata_lines = [
        f"started_local={datetime.now().astimezone().isoformat()}", f"project_root={project_root}",
        f"python={python}", f"python_version={sys.version.split()[0]}", f"repo={repo}",
        f"data_root={data_root}", f"manifest={manifest}", f"ffprobe={ffprobe}",
        f"ffprobe_version={ffprobe_version}", f"analysis_output_dir={output_path}",
        "encoders=sync,async", "repeats_per_encoder=3",
        "passes=sync_r1,async_r1,async_r2,sync_r2,sync_r3,async_r3",
        "canvas_width=640", "jpeg_quality=0.92", "callback_gate_ms=0",
        "initial_frame_policy=paused_draw_encode_before_play",
        "playback_capture=requestVideoFrameCallback.metadata.mediaTime",
        "source_pts=ffprobe_best_effort_timestamp_time", "iot_enabled=false",
        "classifier_checkpoint_loaded=false", "production_code_modified=false",
    ]
    runner_meta.write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")

    print("Running the complete project test suite before Phase 9...", flush=True)
    env = os.environ.copy()
    env["ISL_IOT_ENABLED"] = "false"
    test_result = run_logged([str(python), "-m", "pytest", "-q"], project_root, test_log, env)
    if test_result.returncode != 0:
        raise RuntimeError(f"Complete project test suite failed (exit {test_result.returncode}). Full log: {test_log}")

    run_paths: dict[str, Path] = {}
    pass_results: list[PassResult] = []
    for encoder, repeat, port, run_id in build_passes():
        destination = project_root / "outputs" / f"phase9_capture_{run_id}_{stamp}"
        run_paths[run_id] = destination
        pass_results.append(capture_pass(
            python=python, project_root=project_root, repo=repo, manifest=manifest,
            output_dir=destination, encoder=encoder, repeat=repeat, run_id=run_id,
            port=port, log_root=log_root, target_paths=target_paths,
        ))

    print("\nRunning FFprobe alignment and Phase 9 repeatability analysis...", flush=True)
    analysis_log = log_root / "phase9_analysis.log"
    analysis_command = [
        str(python), str(SCRIPTS / "phase9_repeatability_analysis.py"),
        "--repo", str(repo), "--data-root", str(data_root), "--manifest", str(manifest),
        "--ffprobe", str(ffprobe), "--output-dir", str(output_path),
    ]
    for encoder in ("sync", "async"):
        for repeat in (1, 2, 3):
            run_id = f"{encoder}_r{repeat}"
            analysis_command.extend([f"--{encoder}-r{repeat}-dir", str(run_paths[run_id])])
    analysis_result = run_logged(analysis_command, project_root, analysis_log, env)
    if analysis_result.returncode != 0:
        raise RuntimeError(f"Phase 9 analysis failed (exit {analysis_result.returncode}). Full log: {analysis_log}")

    (stage / "results").mkdir(parents=True, exist_ok=False)
    (stage / "logs").mkdir()
    (stage / "metadata").mkdir()
    (stage / "captures").mkdir()
    for filename in REQUIRED_OUTPUTS:
        path = output_path / filename
        if not path.is_file():
            raise FileNotFoundError(f"Phase 9 required output missing: {path}")
        shutil.copy2(path, stage / "results" / filename)
    for source in log_root.iterdir():
        if source.is_file():
            shutil.copy2(source, stage / "logs" / source.name)
    shutil.copy2(runner_meta, stage / "metadata" / runner_meta.name)
    for item in pass_results:
        target = stage / "captures" / item.run_id
        (target / "clips").mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.output_dir / "capture_index.json", target / "capture_index.json")
        for capture in (item.output_dir / "clips").glob("*.islbrw"):
            shutil.copy2(capture, target / "clips" / capture.name)
    print("Creating and verifying Phase 9 result bundle...", flush=True)
    bundle_hash = create_bundle(stage, bundle, python, project_root)
    print("\nPHASE 9 COMPLETED", flush=True)
    print(f"RESULT_BUNDLE={bundle}", flush=True)
    print(f"BUNDLE_SHA256={bundle_hash}", flush=True)
    print(f"REPORT={output_path / 'PHASE9_REPEATABILITY_REPORT.md'}", flush=True)
    print(f"TEST_LOG={test_log}", flush=True)
    print("IoT stayed disabled. Classifier/checkpoint was not loaded. No production code or settings were changed.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("PHASE9_INTERRUPTED: user cancelled the workflow", file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print(f"PHASE9_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
