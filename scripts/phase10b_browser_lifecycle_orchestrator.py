#!/usr/bin/env python3
"""Run Phase 10B browser focus/visibility lifecycle diagnostics on localhost."""
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

from browser_capture_protocol import normalize_video_path, parse_capture_payload  # noqa: E402
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
    ("foreground_focused", 8831),
    ("visible_unfocused", 8832),
    ("hidden_background", 8833),
    ("focus_recovery", 8834),
)
MODES = tuple(mode for mode, _port in PASS_SPECS)
REQUIRED_OUTPUTS = (
    "phase10b_summary.json",
    "PHASE10B_BROWSER_LIFECYCLE_REPORT.md",
    "phase10b_mode_metrics.csv",
    "phase10b_clip_metrics.csv",
    "phase10b_frame_metrics.csv",
    "phase10b_event_timeline.csv",
    "phase10b_playback_heartbeats.csv",
    "phase10b_hidden_intervals.csv",
    "phase10b_visible_unfocused_intervals.csv",
    "phase10b_source_pts_metadata.csv",
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
        raise RuntimeError("Process environment ISL_IOT_ENABLED is true. Phase 10B is blocked.")
    env_file = project_root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^\s*ISL_IOT_ENABLED\s*=\s*(true|1|yes|on)\s*$", line, re.IGNORECASE):
                raise RuntimeError(".env contains ISL_IOT_ENABLED=true. Phase 10B will not edit it.")
    map_cfg = json.loads((project_root / "config" / "iot_label_map.json").read_text(encoding="utf-8"))
    mappings = map_cfg.get("mappings", {})
    if not isinstance(mappings, dict) or mappings:
        raise RuntimeError("IoT mapping is not empty; Phase 10B requires zero mappings.")
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


def validate_existing_capture_dir(path: Path, *, mode: str, rows: list[dict[str, str]],
                                  target_paths: set[str]) -> dict[str, Any]:
    """Validate and reuse a completed prior pass without recapturing it."""
    directory = path.expanduser().resolve()
    index_path = directory / "capture_index.json"
    if not directory.is_dir() or not index_path.is_file():
        raise RuntimeError(f"Resume directory or capture index is missing: {directory}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    config = index.get("capture_config", {})
    if (config.get("phase10b_mode"), config.get("phase10b_run_id")) != (mode, mode):
        raise RuntimeError(f"Resume capture identity does not match {mode}: {directory}")
    if index.get("official_split_count") != 77 or index.get("received_count") != 6:
        raise RuntimeError(f"Resume capture index must contain six of 77 official clips: {directory}")
    records = index.get("received", [])
    if not isinstance(records, list) or len(records) != 6:
        raise RuntimeError(f"Resume capture index must list exactly six captures: {directory}")
    row_by_path = {target_key(row["video_path"]): row for row in rows}
    seen: set[str] = set()
    for record in records:
        key = target_key(str(record.get("video_path", "")))
        if key not in target_paths or key in seen:
            raise RuntimeError(f"Resume capture contains an unexpected or duplicate validation path: {record.get('video_path')}")
        seen.add(key)
        relative = Path(str(record.get("container", "")).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe container path in resume capture index: {relative}")
        container = (directory / relative).resolve()
        try:
            container.relative_to(directory)
        except ValueError as exc:
            raise RuntimeError(f"Resume capture container escapes its directory: {container}") from exc
        if not container.is_file():
            raise RuntimeError(f"Resume capture container missing: {container}")
        parsed = parse_capture_payload(container.read_bytes(), row_by_path[key])
        if parsed.sha256 != record.get("sha256"):
            raise RuntimeError(f"Resume capture SHA-256 mismatch: {container}")
        case = parsed.header.get("case", {})
        cfg = parsed.header.get("capture_config", {})
        if (case.get("phase10b_mode"), case.get("phase10b_run_id")) != (mode, mode):
            raise RuntimeError(f"Resume clip header identity mismatch: {container}")
        if (cfg.get("phase10b_mode"), cfg.get("phase10b_run_id")) != (mode, mode):
            raise RuntimeError(f"Resume clip config identity mismatch: {container}")
        validation = case.get("phase10b_condition_validation")
        if not isinstance(validation, dict) or validation.get("valid") is not True:
            raise RuntimeError(f"Resume clip lacks successful client-side condition evidence: {container}")
        if not parsed.header.get("case", {}).get("frames") or parsed.header["case"]["frames"][0].get("capture_kind") != "initial_paused":
            raise RuntimeError(f"Resume clip does not begin with the required initial paused frame: {container}")
    if seen != target_paths:
        missing = sorted(target_paths - seen)
        raise RuntimeError(f"Resume capture is incomplete; missing targets: {missing}")
    files = list((directory / "clips").glob("*.islbrw"))
    if len(files) != 6:
        raise RuntimeError(f"Resume capture directory must contain exactly six .islbrw files; found {len(files)}")
    return {"mode": mode, "run_id": mode, "port": None, "output_dir": directory,
            "received": 6, "reused": True}


def capture_mode(*, python: Path, project_root: Path, repo: Path, manifest: Path, output_dir: Path,
                 mode: str, port: int, log_root: Path, target_paths: set[str]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    run_id = mode
    stdout_path = log_root / f"{mode}_server.stdout.log"
    stderr_path = log_root / f"{mode}_server.stderr.log"
    command = [
        str(python), "-u", str(SCRIPTS / "serve_phase10b_capture.py"),
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
                    raise RuntimeError(f"Phase 10B {mode} receiver exited before ready: {tail}")
                try:
                    status = http_json(base_url + "/api/status", timeout=2.5)
                    manifest_status = http_json(base_url + "/api/manifest", timeout=2.5)
                    if status.get("expected_count") == 77 and manifest_status.get("expected_count") == 77:
                        ready = True; break
                except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(1)
            if not ready:
                raise RuntimeError(f"Phase 10B {mode} receiver did not become ready. Check {stdout_path} and {stderr_path}")

            guidance = {
                "foreground_focused": (
                    "Keep this browser tab visible and focused for each clip. Do not switch applications or tabs. "
                    "A clip that loses focus/visibility is rejected; use the Retry button after correcting the condition."
                ),
                "visible_unfocused": (
                    "Arrange the browser and a second app side-by-side. After you click Capture, the page shows ARMED. Click the second app while keeping the capture page visible; that focus-loss event starts the video automatically. "
                    "Stay in the second app until the page reports at least 1.5 seconds of observed visible/unfocused heartbeat evidence. Do not minimize or cover the browser page. "
                    "You may remain unfocused through subsequent clips; those clips start automatically while the page is already visible/unfocused. Rejected attempts are saved to the receiver for diagnosis."
                ),
                "hidden_background": (
                    "Open a second tab before starting. For each clip, wait until its initial paused frame is recorded, switch to the second tab, and remain there for at least 2 seconds from the moment this capture tab becomes hidden. "
                    "If the video ends while hidden, the capture page deliberately keeps listening; return to this tab after the 2-second hidden period so the interval is closed by a visible transition. "
                    "Each clip requires a hidden interval of at least 1.5 seconds that began during playback and was closed by your return. Invalid clips must be retried."
                ),
                "focus_recovery": (
                    "For every clip, wait for the amber prompt (about 0.5 s after playback starts), switch to a second app for at least 0.5 seconds, "
                    "return to the capture page, then click 'Log focus recovery interaction'. The click must be trusted, visible and focused. Invalid clips must be retried."
                ),
            }[mode]
            print("", flush=True)
            print(f"BROWSER ACTION REQUIRED — PHASE 10B: {mode}", flush=True)
            print("1. Select C:\\AIAssets\\INCLUDE_videos.", flush=True)
            print("2. Confirm all six diagnostic clips are matched.", flush=True)
            print("3. Follow this lifecycle condition: " + guidance, flush=True)
            print("4. Click the displayed Capture six clips button.", flush=True)
            print("The browser rejects invalid clips and offers a per-clip Retry button (maximum five attempts). Keep this PowerShell window running until this mode reaches 6/6.", flush=True)
            print(f"Phase 10B {mode} captures received: 0 / 6", flush=True)
            last_count = 0
            deadline = time.monotonic() + 20 * 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(f"Phase 10B {mode} receiver exited during capture: {tail}")
                status = http_json(base_url + "/api/phase10b-status", timeout=5)
                if status.get("abort_reason"):
                    diagnostic_count = int(status.get("rejected_attempt_diagnostic_count", 0) or 0)
                    raise RuntimeError(
                        f"Phase 10B {mode} pass was aborted after condition retries: {status['abort_reason']}; "
                        f"rejected_attempt_diagnostics={diagnostic_count}; raw evidence retained at {output_dir}"
                    )
                received = status.get("received", [])
                paths = {target_key(str(item.get("video_path", ""))) for item in received if isinstance(item, dict)}
                count = len(paths & target_paths)
                if count != last_count:
                    print(f"Phase 10B {mode} captures received: {count} / 6", flush=True)
                    last_count = count
                if count == 6:
                    time.sleep(1.0)
                    break
                time.sleep(1.5)
            if last_count != 6:
                raise TimeoutError(f"Timed out waiting for Phase 10B {mode}: received {last_count}/6. Data remains at {output_dir}")
        finally:
            stop_process(process)

    index_path = output_dir / "capture_index.json"
    if not index_path.is_file():
        raise RuntimeError(f"Phase 10B {mode} capture index was not saved")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("received_count") != 6:
        raise RuntimeError(f"Phase 10B {mode} index received_count={index.get('received_count')}; expected 6")
    cfg = index.get("capture_config", {})
    if (cfg.get("phase10b_mode"), cfg.get("phase10b_run_id")) != (mode, run_id):
        raise RuntimeError(f"Phase 10B {mode} saved capture-index identity mismatch")
    files = list((output_dir / "clips").glob("*.islbrw"))
    if len(files) != 6:
        raise RuntimeError(f"Phase 10B {mode} expected six .islbrw files, found {len(files)}")
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
            raise FileNotFoundError(f"Required Phase 10B output missing: {source}")
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
        attempt_diagnostics = source_dir / "phase10b_attempt_diagnostics.jsonl"
        if attempt_diagnostics.is_file():
            shutil.copy2(attempt_diagnostics, destination / "phase10b_attempt_diagnostics.jsonl")
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
    verifier = project_root / "scripts" / "verify_phase10b_bundle.py"
    check = subprocess.run([str(python), str(verifier), str(bundle)], cwd=project_root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    print(check.stdout.strip(), flush=True)
    if check.returncode:
        raise RuntimeError(f"Phase 10B bundle verification failed: {check.stderr[-2000:]}")
    return sha256_file(bundle)


def run(args: argparse.Namespace) -> int:
    project = ROOT.resolve(); python = Path(sys.executable).resolve()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"Phase 10B requires Python 3.12; found {sys.version_info.major}.{sys.version_info.minor}")
    repo = args.repo.resolve(); data_root = args.data_root.resolve(); manifest = args.manifest.resolve(); ffprobe = args.ffprobe.resolve()
    for path, label in ((repo, "AI4Bharat repository"), (data_root, "INCLUDE video root"), (manifest, "official validation manifest"), (ffprobe, "FFprobe executable")):
        if not path.exists():
            raise FileNotFoundError(f"{label} missing: {path}")
    if not (SCRIPTS / "serve_phase10b_capture.py").is_file() or not (SCRIPTS / "phase10b_browser_lifecycle_analysis.py").is_file():
        raise FileNotFoundError("Phase 10B receiver or analyzer is missing")
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
        raise RuntimeError(f"Phase 10B diagnostic targets missing from pinned manifest: {missing}")
    resumed_foreground_candidate = None
    if args.resume_foreground_dir is not None:
        resumed_foreground_candidate = validate_existing_capture_dir(
            args.resume_foreground_dir, mode="foreground_focused", rows=rows, target_paths=target_paths)
    resumed_visible_unfocused_candidate = None
    if args.resume_visible_unfocused_dir is not None:
        # The visible/unfocused contract is unchanged by v3; reuse only a complete
        # six-clip pass whose client-side condition evidence was already verified.
        resumed_visible_unfocused_candidate = validate_existing_capture_dir(
            args.resume_visible_unfocused_dir, mode="visible_unfocused", rows=rows, target_paths=target_paths)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = project / "outputs" / f"phase10b_browser_lifecycle_{stamp}"
    log_root = project / "outputs" / f"phase10b_run_logs_{stamp}"
    stage = project / "outputs" / f"PHASE10B_BUNDLE_STAGE_{stamp}"
    bundle = project / "outputs" / f"PHASE10B_RESULT_BUNDLE_{stamp}.zip"
    output_dir.mkdir(parents=True, exist_ok=False); log_root.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy(); env["ISL_IOT_ENABLED"] = "false"
    test_log = log_root / "pytest_full_suite.log"
    print("Running the complete project test suite before Phase 10B...", flush=True)
    test = run_logged([str(python), "-m", "pytest", "-q"], project, test_log, env)
    if test.returncode:
        raise RuntimeError(f"Project test suite failed ({test.returncode}); see {test_log}")
    environment = [
        f"started_local={datetime.now().astimezone().isoformat()}", f"python={python}", f"python_version={sys.version.split()[0]}",
        f"repo={repo}", f"data_root={data_root}", f"manifest={manifest}", f"ffprobe={ffprobe}", f"ffprobe_version={ffprobe_version}",
        "modes=" + ",".join(mode for mode, _ in PASS_SPECS), "encoder=sync_toDataURL", "canvas_width=640", "jpeg_quality=0.92",
        "callback_gate_ms=0", "initial_frame_policy=paused_draw_encode_before_play", "callback=requestVideoFrameCallback.metadata.mediaTime",
        "heartbeat_interval_ms=100", "condition_validation=client_and_python_independent", "hidden_interval_semantics=must_begin_during_playback_and_close_on_visible_return", "per_clip_retry_limit=5", "iot_enabled=false", "classifier_checkpoint_loaded=false", "production_code_modified=false",
    ]
    if args.resume_foreground_dir is not None:
        environment.append(f"reused_foreground_capture_dir={args.resume_foreground_dir.resolve()}")
    if args.resume_visible_unfocused_dir is not None:
        environment.append(f"reused_visible_unfocused_capture_dir={args.resume_visible_unfocused_dir.resolve()}")
    (log_root / "run_environment.txt").write_text("\n".join(environment) + "\n", encoding="utf-8")
    pass_results: list[dict[str, Any]] = []
    resumed_foreground = resumed_foreground_candidate
    resumed_visible_unfocused = resumed_visible_unfocused_candidate
    for reused_mode, reused in (("foreground_focused", resumed_foreground),
                                ("visible_unfocused", resumed_visible_unfocused)):
        if reused is None:
            continue
        pass_results.append(reused)
        print(f"Reusing verified {reused_mode} captures from: {reused['output_dir']}", flush=True)
        prefix = f"phase10b_capture_{reused_mode}_"
        prior_stamp = reused["output_dir"].name[len(prefix):] if reused["output_dir"].name.startswith(prefix) else ""
        prior_logs = project / "outputs" / f"phase10b_run_logs_{prior_stamp}"
        if prior_logs.is_dir():
            for source in prior_logs.iterdir():
                if source.is_file():
                    shutil.copy2(source, log_root / f"reused_{reused_mode}_{source.name}")
    for mode, port in PASS_SPECS:
        if mode == "foreground_focused" and resumed_foreground is not None:
            continue
        if mode == "visible_unfocused" and resumed_visible_unfocused is not None:
            continue
        out = project / "outputs" / f"phase10b_capture_{mode}_{stamp}"
        result = capture_mode(python=python, project_root=project, repo=repo, manifest=manifest, output_dir=out,
                              mode=mode, port=port, log_root=log_root, target_paths=target_paths)
        pass_results.append(result)
    print("\nRunning Phase 10B FFprobe lifecycle analysis...", flush=True)
    analysis_log = log_root / "phase10b_analysis.log"
    command = [str(python), str(SCRIPTS / "phase10b_browser_lifecycle_analysis.py"),
               "--repo", str(repo), "--data-root", str(data_root), "--manifest", str(manifest),
               "--ffprobe", str(ffprobe), "--output-dir", str(output_dir)]
    for mode, _port in PASS_SPECS:
        path = next(item["output_dir"] for item in pass_results if item["mode"] == mode)
        command.extend([f"--{mode.replace('_', '-')}-dir", str(path)])
    analysis = run_logged(command, project, analysis_log, env)
    if analysis.returncode:
        raise RuntimeError(f"Phase 10B analysis failed ({analysis.returncode}); see {analysis_log}")
    # Bundle metadata pointers are consumed only within the bundle creation function and then removed.
    (stage / "metadata").mkdir(parents=True, exist_ok=False)
    (stage / "metadata" / "analysis_output_dir.txt").write_text(str(output_dir), encoding="utf-8")
    (stage / "metadata" / "log_root.txt").write_text(str(log_root), encoding="utf-8")
    for item in pass_results:
        (stage / "metadata" / f"capture_{item['mode']}.txt").write_text(str(item["output_dir"]), encoding="utf-8")
    (stage / "metadata" / "run_environment.txt").write_text("\n".join(environment) + "\n", encoding="utf-8")
    (stage / "metadata" / "pytest_exit_code.txt").write_text(str(test.returncode), encoding="utf-8")
    print("Creating and verifying Phase 10B result bundle...", flush=True)
    bundle_hash = create_bundle(stage, bundle, python, project)
    print("\nPHASE10B_STATUS=completed", flush=True)
    print(f"MODES={len(pass_results)}/4 | CLIPS={len(pass_results)*6}/24", flush=True)
    print(f"OUTPUT={output_dir}", flush=True)
    print(f"RESULT_BUNDLE={bundle}", flush=True)
    print(f"BUNDLE_SHA256={bundle_hash}", flush=True)
    print(f"REPORT={output_dir / 'PHASE10B_BROWSER_LIFECYCLE_REPORT.md'}", flush=True)
    print(f"TEST_LOG={test_log}", flush=True)
    print("IoT disabled; classifier/checkpoint not loaded; production code unchanged.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--resume-foreground-dir", type=Path, default=None,
                        help="Reuse a previously completed and independently verified foreground_focused six-clip pass.")
    parser.add_argument("--resume-visible-unfocused-dir", type=Path, default=None,
                        help="Reuse a previously completed visible_unfocused pass; this condition's contract is unchanged in v3.")
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("PHASE10B_INTERRUPTED: workflow cancelled by user", file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print(f"PHASE10B_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
