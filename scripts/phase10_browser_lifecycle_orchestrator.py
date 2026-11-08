#!/usr/bin/env python3
"""Run Phase 10 browser focus/visibility lifecycle diagnostics on localhost."""
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
    ("foreground_focused", 8811),
    ("visible_unfocused", 8812),
    ("hidden_background", 8813),
    ("focus_recovery", 8814),
)
MODES = tuple(mode for mode, _port in PASS_SPECS)
REQUIRED_OUTPUTS = (
    "phase10_summary.json",
    "PHASE10_BROWSER_LIFECYCLE_REPORT.md",
    "phase10_mode_metrics.csv",
    "phase10_clip_metrics.csv",
    "phase10_frame_metrics.csv",
    "phase10_event_timeline.csv",
    "phase10_playback_heartbeats.csv",
    "phase10_source_pts_metadata.csv",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_safety(project_root: Path) -> None:
    if truthy(os.environ.get("ISL_IOT_ENABLED")):
        raise RuntimeError("Process environment ISL_IOT_ENABLED is true. Phase 10 is blocked.")
    env_file = project_root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$", line, re.IGNORECASE):
                raise RuntimeError(".env contains ISL_IOT_ENABLED=true. Phase 10 will not edit it.")
    map_cfg = json.loads((project_root / "config" / "iot_label_map.json").read_text(encoding="utf-8"))
    mappings = map_cfg.get("mappings", {})
    if not isinstance(mappings, dict) or mappings:
        raise RuntimeError("IoT mapping is not empty; Phase 10 requires zero mappings.")
    app_cfg = (project_root / "src" / "isl_recognition" / "app_config.py").read_text(encoding="utf-8")
    if not re.search(r"pre_iot_lockdown\s*:\s*bool\s*=\s*True", app_cfg):
        raise RuntimeError("Hard pre-IoT application lockdown was not found.")
    os.environ["ISL_IOT_ENABLED"] = "false"


def http_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run_logged(command: list[str], cwd: Path, log_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", check=False)
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-28:]:
        print(line, flush=True)
    return result


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
            process.kill(); process.wait(timeout=5)
        except Exception:
            pass


def target_key(path: str) -> str:
    return normalize_video_path(path).casefold()


def capture_mode(*, python: Path, project_root: Path, repo: Path, manifest: Path, output_dir: Path,
                 mode: str, port: int, log_root: Path, target_paths: set[str]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    run_id = mode
    stdout_path = log_root / f"{mode}_server.stdout.log"
    stderr_path = log_root / f"{mode}_server.stderr.log"
    command = [
        str(python), "-u", str(SCRIPTS / "serve_phase10_capture.py"),
        "--manifest", str(manifest), "--repo", str(repo), "--output", str(output_dir),
        "--mode", mode, "--run-id", run_id, "--port", str(port), "--max-upload-mb", "512", "--open-browser",
    ]
    env = os.environ.copy(); env["ISL_IOT_ENABLED"] = "false"
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        process = subprocess.Popen(command, cwd=project_root, env=env, stdout=out, stderr=err,
                                   text=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            base_url = f"http://127.0.0.1:{port}"
            ready = False
            for _ in range(60):
                if process.poll() is not None:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(f"Phase 10 {mode} receiver exited before ready: {tail}")
                try:
                    status = http_json(base_url + "/api/status", timeout=2.5)
                    manifest_status = http_json(base_url + "/api/manifest", timeout=2.5)
                    if status.get("expected_count") == 77 and manifest_status.get("expected_count") == 77:
                        ready = True; break
                except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(1)
            if not ready:
                raise RuntimeError(f"Phase 10 {mode} receiver did not become ready. Check {stdout_path} and {stderr_path}")

            guidance = {
                "foreground_focused": (
                    "Keep the browser tab visible and the browser window focused for all six clips. "
                    "Do not switch applications or tabs during playback."
                ),
                "visible_unfocused": (
                    "Arrange browser and a second application side-by-side. After pressing Capture, click the second app. "
                    "Keep the browser page visible and do not minimize/cover it for the six-clip pass."
                ),
                "hidden_background": (
                    "Before pressing Capture, open a second blank tab (Ctrl+T), then return to this capture tab. "
                    "After pressing Capture, wait until the browser diagnostic log says the initial paused frame was recorded, then press Ctrl+Tab. "
                    "Keep the capture tab hidden for about 8 seconds, then return and let the six-clip pass finish."
                ),
                "focus_recovery": (
                    "For each clip, wait for the amber focus prompt (~0.7 s after playback starts), switch to a second app for about 1 s, "
                    "return to the capture page, then click 'Log focus recovery interaction'. Repeat for all six clips."
                ),
            }[mode]
            print("", flush=True)
            print(f"BROWSER ACTION REQUIRED — PHASE 10: {mode}", flush=True)
            print("1. Select C:\\AIAssets\\INCLUDE_videos.", flush=True)
            print("2. Confirm all six diagnostic clips are matched.", flush=True)
            print("3. Follow this lifecycle condition: " + guidance, flush=True)
            print("4. Click the displayed Capture six clips button.", flush=True)
            print("Keep this PowerShell window running until this mode reaches 6/6.", flush=True)
            print(f"Phase 10 {mode} captures received: 0 / 6", flush=True)
            last_count = 0
            deadline = time.monotonic() + 20 * 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(f"Phase 10 {mode} receiver exited during capture: {tail}")
                status = http_json(base_url + "/api/status", timeout=5)
                received = status.get("received", [])
                paths = {target_key(str(item.get("video_path", ""))) for item in received if isinstance(item, dict)}
                count = len(paths & target_paths)
                if count != last_count:
                    print(f"Phase 10 {mode} captures received: {count} / 6", flush=True)
                    last_count = count
                if count == 6:
                    time.sleep(1.0)
                    break
                time.sleep(1.5)
            if last_count != 6:
                raise TimeoutError(f"Timed out waiting for Phase 10 {mode}: received {last_count}/6. Data remains at {output_dir}")
        finally:
            stop_process(process)

    index_path = output_dir / "capture_index.json"
    if not index_path.is_file():
        raise RuntimeError(f"Phase 10 {mode} capture index was not saved")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("received_count") != 6:
        raise RuntimeError(f"Phase 10 {mode} index received_count={index.get('received_count')}; expected 6")
    cfg = index.get("capture_config", {})
    if (cfg.get("phase10_mode"), cfg.get("phase10_run_id")) != (mode, run_id):
        raise RuntimeError(f"Phase 10 {mode} saved capture-index identity mismatch")
    files = list((output_dir / "clips").glob("*.islbrw"))
    if len(files) != 6:
        raise RuntimeError(f"Phase 10 {mode} expected six .islbrw files, found {len(files)}")
    return {"mode": mode, "run_id": run_id, "port": port, "output_dir": output_dir,
            "stdout_log": stdout_path, "stderr_log": stderr_path, "received": 6}


def create_bundle(stage: Path, bundle: Path, python: Path, project_root: Path) -> str:
    stage.mkdir(parents=True, exist_ok=True)
    for name in ("results", "logs", "metadata", "captures"):
        (stage / name).mkdir(exist_ok=True)
    # The orchestrator writes the required result path into metadata before this function is called.
    results_source = Path((stage / "metadata" / "analysis_output_dir.txt").read_text(encoding="utf-8").strip())
    for filename in REQUIRED_OUTPUTS:
        source = results_source / filename
        if not source.is_file():
            raise FileNotFoundError(f"Required Phase 10 output missing: {source}")
        shutil.copy2(source, stage / "results" / filename)
    log_root = Path((stage / "metadata" / "log_root.txt").read_text(encoding="utf-8").strip())
    for source in log_root.iterdir():
        if source.is_file():
            shutil.copy2(source, stage / "logs" / source.name)
    for mode in MODES:
        source_dir = Path((stage / "metadata" / f"capture_{mode}.txt").read_text(encoding="utf-8").strip())
        destination = stage / "captures" / mode
        (destination / "clips").mkdir(parents=True)
        shutil.copy2(source_dir / "capture_index.json", destination / "capture_index.json")
        for file in (source_dir / "clips").glob("*.islbrw"):
            shutil.copy2(file, destination / "clips" / file.name)
    # Replace pointer files with real run metadata.
    for pointer in (stage / "metadata" / "analysis_output_dir.txt", stage / "metadata" / "log_root.txt", *[stage / "metadata" / f"capture_{mode}.txt" for mode in MODES]):
        pointer.unlink(missing_ok=True)
    hash_lines = []
    for file in sorted(p for p in stage.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"):
        rel = file.relative_to(stage).as_posix()
        hash_lines.append(f"{sha256_file(file)}  {rel}")
    (stage / "SHA256SUMS.txt").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(p for p in stage.rglob("*") if p.is_file()):
            archive.write(file, file.relative_to(stage).as_posix())
    verifier = project_root / "scripts" / "verify_phase10_bundle.py"
    check = subprocess.run([str(python), str(verifier), str(bundle)], cwd=project_root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print(check.stdout.strip(), flush=True)
    if check.returncode:
        raise RuntimeError(f"Phase 10 bundle verification failed: {check.stderr[-2000:]}")
    return sha256_file(bundle)


def run(args: argparse.Namespace) -> int:
    project = ROOT.resolve(); python = Path(sys.executable).resolve()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 10 requires Python 3.12; found {sys.version_info.major}.{sys.version_info.minor}")
    repo = args.repo.resolve(); data_root = args.data_root.resolve(); manifest = args.manifest.resolve(); ffprobe = args.ffprobe.resolve()
    for path, label in ((repo, "AI4Bharat repository"), (data_root, "INCLUDE video root"), (manifest, "official validation manifest"), (ffprobe, "FFprobe executable")):
        if not path.exists():
            raise FileNotFoundError(f"{label} missing: {path}")
    if not (SCRIPTS / "serve_phase10_capture.py").is_file() or not (SCRIPTS / "phase10_browser_lifecycle_analysis.py").is_file():
        raise FileNotFoundError("Phase 10 receiver or analyzer is missing")
    validate_safety(project)
    version = subprocess.run([str(ffprobe), "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if version.returncode or "ffprobe version" not in version.stdout:
        raise RuntimeError(f"FFprobe preflight failed: {(version.stdout + version.stderr)[-1500:]}")
    ffprobe_version = version.stdout.splitlines()[0]
    rows = read_and_verify_manifest(manifest, repo, project / "config" / "label_map_include50.json")
    if len(rows) != 77:
        raise RuntimeError(f"Official validation manifest must have 77 rows; found {len(rows)}")
    manifest_paths = {target_key(r["video_path"]) for r in rows}
    target_paths = {target_key(p) for p in TARGET_PATHS}
    missing = sorted(target_paths - manifest_paths)
    if missing:
        raise RuntimeError(f"Phase 10 diagnostic targets missing from pinned manifest: {missing}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = project / "outputs" / f"phase10_browser_lifecycle_{stamp}"
    log_root = project / "outputs" / f"phase10_run_logs_{stamp}"
    stage = project / "outputs" / f"PHASE10_BUNDLE_STAGE_{stamp}"
    bundle = project / "outputs" / f"PHASE10_RESULT_BUNDLE_{stamp}.zip"
    output_dir.mkdir(parents=True, exist_ok=False); log_root.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy(); env["ISL_IOT_ENABLED"] = "false"
    test_log = log_root / "pytest_full_suite.log"
    print("Running the complete project test suite before Phase 10...", flush=True)
    test = run_logged([str(python), "-m", "pytest", "-q"], project, test_log, env)
    if test.returncode:
        raise RuntimeError(f"Project test suite failed ({test.returncode}); see {test_log}")
    environment = [
        f"started_local={datetime.now().astimezone().isoformat()}", f"python={python}", f"python_version={sys.version.split()[0]}",
        f"repo={repo}", f"data_root={data_root}", f"manifest={manifest}", f"ffprobe={ffprobe}", f"ffprobe_version={ffprobe_version}",
        "modes=" + ",".join(mode for mode, _ in PASS_SPECS), "encoder=sync_toDataURL", "canvas_width=640", "jpeg_quality=0.92",
        "callback_gate_ms=0", "initial_frame_policy=paused_draw_encode_before_play", "callback=requestVideoFrameCallback.metadata.mediaTime",
        "heartbeat_interval_ms=100", "iot_enabled=false", "classifier_checkpoint_loaded=false", "production_code_modified=false",
    ]
    (log_root / "run_environment.txt").write_text("\n".join(environment) + "\n", encoding="utf-8")
    pass_results: list[dict[str, Any]] = []
    for mode, port in PASS_SPECS:
        out = project / "outputs" / f"phase10_capture_{mode}_{stamp}"
        result = capture_mode(python=python, project_root=project, repo=repo, manifest=manifest, output_dir=out,
                              mode=mode, port=port, log_root=log_root, target_paths=target_paths)
        pass_results.append(result)
    print("\nRunning Phase 10 FFprobe lifecycle analysis...", flush=True)
    analysis_log = log_root / "phase10_analysis.log"
    command = [str(python), str(SCRIPTS / "phase10_browser_lifecycle_analysis.py"),
               "--repo", str(repo), "--data-root", str(data_root), "--manifest", str(manifest),
               "--ffprobe", str(ffprobe), "--output-dir", str(output_dir)]
    for mode, _port in PASS_SPECS:
        path = next(item["output_dir"] for item in pass_results if item["mode"] == mode)
        command.extend([f"--{mode.replace('_', '-')}-dir", str(path)])
    analysis = run_logged(command, project, analysis_log, env)
    if analysis.returncode:
        raise RuntimeError(f"Phase 10 analysis failed ({analysis.returncode}); see {analysis_log}")
    # Bundle metadata pointers are consumed only within the bundle creation function and then removed.
    (stage / "metadata").mkdir(parents=True, exist_ok=False)
    (stage / "metadata" / "analysis_output_dir.txt").write_text(str(output_dir), encoding="utf-8")
    (stage / "metadata" / "log_root.txt").write_text(str(log_root), encoding="utf-8")
    for item in pass_results:
        (stage / "metadata" / f"capture_{item['mode']}.txt").write_text(str(item["output_dir"]), encoding="utf-8")
    (stage / "metadata" / "run_environment.txt").write_text("\n".join(environment) + "\n", encoding="utf-8")
    (stage / "metadata" / "pytest_exit_code.txt").write_text(str(test.returncode), encoding="utf-8")
    print("Creating and verifying Phase 10 result bundle...", flush=True)
    bundle_hash = create_bundle(stage, bundle, python, project)
    print("\nPHASE10_STATUS=completed", flush=True)
    print(f"MODES={len(pass_results)}/4 | CLIPS={len(pass_results)*6}/24", flush=True)
    print(f"OUTPUT={output_dir}", flush=True)
    print(f"RESULT_BUNDLE={bundle}", flush=True)
    print(f"BUNDLE_SHA256={bundle_hash}", flush=True)
    print(f"REPORT={output_dir / 'PHASE10_BROWSER_LIFECYCLE_REPORT.md'}", flush=True)
    print(f"TEST_LOG={test_log}", flush=True)
    print("IoT disabled; classifier/checkpoint not loaded; production code unchanged.", flush=True)
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
        print("PHASE10_INTERRUPTED: workflow cancelled by user", file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print(f"PHASE10_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
