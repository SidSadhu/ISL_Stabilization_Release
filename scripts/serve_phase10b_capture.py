#!/usr/bin/env python3
"""Loopback-only Phase 10B receiver; accepts only condition-verified captures."""
from __future__ import annotations
import argparse
import io
import json
import os
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import MAGIC, PREFIX, normalize_video_path  # noqa: E402
from serve_browser_validation_capture import CaptureServer, Handler as BaseHandler, read_and_verify_manifest  # noqa: E402

MODES = ("foreground_focused", "visible_unfocused", "hidden_background", "focus_recovery")


class Phase10BCaptureServer(CaptureServer):
    def __init__(self, *args, mode: str, run_id: str, **kwargs):
        self.phase10b_mode = mode
        self.phase10b_run_id = run_id
        super().__init__(*args, **kwargs)

    def persist_index(self) -> None:
        super().persist_index()
        target = self.output_dir / "capture_index.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["capture_config"].update({
            "phase": "10B",
            "phase10b_mode": self.phase10b_mode,
            "phase10b_run_id": self.phase10b_run_id,
            "phase10b_capture_encoder": "sync_toDataURL",
            "phase10b_callback_gate_ms": 0,
            "timer_interval_ms": 40,  # Required ISLBRW01 protocol metadata; does not gate rVFC callbacks.
            "heartbeat_interval_ms": 100,
            "capture_sampling_mode": "requestVideoFrameCallback",
            "phase10b_initial_frame_policy": "paused_draw_encode_before_play",
            "phase10b_condition_enforcement_version": 2,
            "condition_retry_max_attempts": 5,
            "iot_enabled": False,
            "classifier_checkpoint_loaded": False,
            "production_code_modified": False,
        })
        temp = target.with_suffix(".json.phase10b.tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, target)


class Phase10BHandler(BaseHandler):
    def do_GET(self):  # noqa: N802
        request_path = urlparse(self.path).path
        if request_path == "/api/phase10b-status":
            body = self.server.snapshot()
            body["abort_reason"] = getattr(self.server, "phase10b_abort_reason", None)
            body["phase10b_mode"] = self.server.phase10b_mode
            diagnostic_path = self.server.output_dir / "phase10b_attempt_diagnostics.jsonl"
            if diagnostic_path.is_file():
                with self.server.lock:
                    body["rejected_attempt_diagnostic_count"] = sum(1 for line in diagnostic_path.open("r", encoding="utf-8") if line.strip())
            else:
                body["rejected_attempt_diagnostic_count"] = 0
            self._json(200, body)
            return
        if request_path == "/":
            template = (SCRIPTS / "browser_validation_capture_phase10b.html").read_text(encoding="utf-8")
            page = template.replace("__PHASE10B_MODE__", json.dumps(self.server.phase10b_mode))
            page = page.replace("__PHASE10B_RUN_ID__", json.dumps(self.server.phase10b_run_id))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        request_path = urlparse(self.path).path
        if request_path == "/api/attempt-diagnostic":
            try:
                size = int(self.headers.get("Content-Length") or "0")
                if size < 2 or size > 2 * 1024 * 1024:
                    self._json(413, {"error": "invalid_phase10b_attempt_diagnostic_length"})
                    return
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if (body.get("mode"), body.get("run_id")) != (self.server.phase10b_mode, self.server.phase10b_run_id):
                    self._json(400, {"error": "phase10b_attempt_diagnostic_identity_mismatch"})
                    return
                video_path = normalize_video_path(str(body.get("video_path") or ""))
                if video_path.casefold() not in self.server.row_by_path:
                    self._json(400, {"error": "phase10b_attempt_diagnostic_path_not_in_manifest"})
                    return
                attempt = body.get("attempt_number")
                if not isinstance(attempt, int) or not 1 <= attempt <= 5:
                    self._json(400, {"error": "phase10b_attempt_diagnostic_invalid_attempt"})
                    return
                if not isinstance(body.get("reason"), str) or not isinstance(body.get("evidence"), dict):
                    self._json(400, {"error": "phase10b_attempt_diagnostic_missing_reason_or_evidence"})
                    return
                record = {
                    "received_at_utc": datetime.now(timezone.utc).isoformat(),
                    **body,
                }
                destination = self.server.output_dir / "phase10b_attempt_diagnostics.jsonl"
                line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                with self.server.lock:
                    with destination.open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(line)
                        stream.flush()
                        os.fsync(stream.fileno())
                self._json(200, {"status": "attempt_diagnostic_saved", "attempt_number": attempt,
                                 "mode": self.server.phase10b_mode})
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": "invalid_phase10b_attempt_diagnostic: " + str(exc)})
            return
        if request_path == "/api/abort":
            try:
                size = int(self.headers.get("Content-Length") or "0")
                if size < 2 or size > 8192:
                    self._json(400, {"error": "invalid_phase10b_abort_length"})
                    return
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if (body.get("mode"), body.get("run_id")) != (self.server.phase10b_mode, self.server.phase10b_run_id):
                    self._json(400, {"error": "phase10b_abort_identity_mismatch"})
                    return
                self.server.phase10b_abort_reason = str(body.get("reason") or "capture pass aborted")[:2000]
                self._json(200, {"status": "abort_recorded"})
            except Exception as exc:
                self._json(400, {"error": "invalid_phase10b_abort: " + str(exc)})
            return
        if request_path != "/api/capture":
            super().do_POST()
            return
        try:
            size = int(self.headers.get("Content-Length") or "0")
            if size < 12 or size > self.server.max_upload_bytes:
                self._json(413, {"error": "invalid_phase10b_upload_length"})
                return
            payload = self.rfile.read(size)
            if len(payload) != size or payload[:8] != MAGIC:
                self._json(400, {"error": "invalid_phase10b_capture_payload"})
                return
            header_length = PREFIX.unpack_from(payload, 8)[0]
            if header_length < 2 or 12 + header_length > len(payload):
                self._json(400, {"error": "invalid_phase10b_header_length"})
                return
            header = json.loads(payload[12:12 + header_length].decode("utf-8"))
            case = header.get("case", {})
            config = header.get("capture_config", {})
            expected = (self.server.phase10b_mode, self.server.phase10b_run_id)
            case_identity = (case.get("phase10b_mode"), case.get("phase10b_run_id"))
            config_identity = (config.get("phase10b_mode"), config.get("phase10b_run_id"))
            if case_identity != expected or config_identity != expected:
                self._json(400, {"error": "phase10b_run_identity_mismatch", "expected": expected,
                                 "case_identity": case_identity, "config_identity": config_identity})
                return
            if case.get("phase10b_capture_encoder") != "sync":
                self._json(400, {"error": "phase10b_encoder_must_be_sync_reference"})
                return
            if case.get("phase10b_initial_frame_policy") != "paused_draw_encode_before_play":
                self._json(400, {"error": "phase10b_initial_frame_policy_mismatch"})
                return
            validation = case.get("phase10b_condition_validation")
            if not isinstance(validation, dict) or validation.get("valid") is not True:
                self._json(422, {"error": "phase10b_condition_not_verified", "validation": validation})
                return
            attempt = case.get("phase10b_attempt_number")
            if not isinstance(attempt, int) or not 1 <= attempt <= 5:
                self._json(400, {"error": "phase10b_invalid_attempt_number"})
                return
            frames = case.get("frames") or []
            if not frames or frames[0].get("capture_kind") != "initial_paused" or abs(float(frames[0].get("video_time_seconds", -1))) > 0.0001:
                self._json(400, {"error": "phase10b_initial_frame_not_first_or_not_zero"})
                return
            events = case.get("timeline_events") or []
            if not any(item.get("event_type") == "capture_finished" for item in events):
                self._json(400, {"error": "phase10b_capture_finished_event_missing"})
                return
            self.rfile = io.BytesIO(payload)
            super().do_POST()
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_phase10b_identity_header: " + str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--max-upload-mb", type=int, default=512)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 1 <= args.max_upload_mb <= 1024:
        parser.error("--max-upload-mb must be between 1 and 1024")
    if args.run_id != args.mode:
        parser.error("--run-id must exactly equal --mode")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        parser.error("ISL_IOT_ENABLED is true; Phase 10B capture is blocked")
    rows = read_and_verify_manifest(args.manifest.expanduser().resolve(), args.repo.expanduser().resolve(), args.label_map.resolve())
    if len(rows) != 77:
        parser.error(f"Pinned official validation manifest must contain 77 rows; found {len(rows)}")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = Phase10BCaptureServer(
        ("127.0.0.1", args.port), Phase10BHandler, rows=rows, output_dir=output,
        overwrite=False, max_upload_bytes=args.max_upload_mb * 1024 * 1024,
        mode=args.mode, run_id=args.run_id,
    )
    server.phase10b_abort_reason = None
    url = f"http://127.0.0.1:{args.port}/"
    print(f"PHASE10B_CAPTURE_SERVER=ready | manifest={len(rows)} | target_clips=6 | mode={args.mode} | run_id={args.run_id}", flush=True)
    print("Bound to 127.0.0.1 only. Receives only condition-verified captures; no classifier or IoT endpoints.", flush=True)
    print(f"URL={url}", flush=True)
    print(f"OUTPUT={output}", flush=True)
    if args.open_browser:
        threading.Timer(0.75, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.persist_index()
        server.server_close()
    snapshot = server.snapshot()
    print(f"RECEIVED={snapshot['received_count']}/6 target clips", flush=True)
    return 0 if snapshot["received_count"] == 6 else 2


if __name__ == "__main__":
    raise SystemExit(main())
