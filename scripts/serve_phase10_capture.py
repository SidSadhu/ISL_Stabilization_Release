#!/usr/bin/env python3
"""Loopback-only Phase 10 receiver for browser lifecycle diagnostics."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import MAGIC, PREFIX  # noqa: E402
from serve_browser_validation_capture import CaptureServer, Handler as BaseHandler, read_and_verify_manifest  # noqa: E402

MODES = ("foreground_focused", "visible_unfocused", "hidden_background", "focus_recovery")


class Phase10CaptureServer(CaptureServer):
    def __init__(self, *args, mode: str, run_id: str, **kwargs):
        self.phase10_mode = mode
        self.phase10_run_id = run_id
        super().__init__(*args, **kwargs)

    def persist_index(self) -> None:
        super().persist_index()
        target = self.output_dir / "capture_index.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["capture_config"].update({
            "phase": 10,
            "phase10_mode": self.phase10_mode,
            "phase10_run_id": self.phase10_run_id,
            "phase10_capture_encoder": "sync",
            "phase10_lifecycle_instrumentation": True,
            "phase10_initial_frame_policy": "paused_draw_encode_before_play",
            "phase10_callback_gate_ms": 0,
            "iot_enabled": False,
            "classifier_checkpoint_loaded": False,
            "production_code_modified": False,
        })
        temp = target.with_suffix(".json.phase10.tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, target)


class Phase10Handler(BaseHandler):
    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/":
            template = (SCRIPTS / "browser_validation_capture_phase10.html").read_text(encoding="utf-8")
            page = template.replace("__PHASE10_MODE__", json.dumps(self.server.phase10_mode))
            page = page.replace("__PHASE10_RUN_ID__", json.dumps(self.server.phase10_run_id))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/capture":
            super().do_POST()
            return
        try:
            size = int(self.headers.get("Content-Length") or "0")
            if size < 12 or size > self.server.max_upload_bytes:
                self._json(413, {"error": "invalid_phase10_upload_length"})
                return
            payload = self.rfile.read(size)
            if len(payload) != size or payload[:8] != MAGIC:
                self._json(400, {"error": "invalid_phase10_capture_payload"})
                return
            header_length = PREFIX.unpack_from(payload, 8)[0]
            if header_length < 2 or 12 + header_length > len(payload):
                self._json(400, {"error": "invalid_phase10_header_length"})
                return
            header = json.loads(payload[12:12 + header_length].decode("utf-8"))
            case = header.get("case", {})
            config = header.get("capture_config", {})
            expected = (self.server.phase10_mode, self.server.phase10_run_id)
            case_identity = (case.get("phase10_mode"), case.get("phase10_run_id"))
            config_identity = (config.get("phase10_mode"), config.get("phase10_run_id"))
            if case_identity != expected or config_identity != expected:
                self._json(400, {"error": "phase10_run_identity_mismatch", "expected": expected,
                                 "case_identity": case_identity, "config_identity": config_identity})
                return
            if case.get("phase10_initial_frame_policy") != "paused_draw_encode_before_play":
                self._json(400, {"error": "phase10_initial_frame_policy_mismatch"})
                return
            if case.get("phase10_capture_encoder") != "sync":
                self._json(400, {"error": "phase10_encoder_must_be_sync_reference"})
                return
            self.rfile = io.BytesIO(payload)
            super().do_POST()
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_phase10_identity_header: " + str(exc)})


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
        parser.error("ISL_IOT_ENABLED is true; Phase 10 capture is blocked")
    rows = read_and_verify_manifest(args.manifest.expanduser().resolve(), args.repo.expanduser().resolve(), args.label_map.resolve())
    if len(rows) != 77:
        parser.error(f"Pinned official validation manifest must contain 77 rows; found {len(rows)}")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = Phase10CaptureServer(
        ("127.0.0.1", args.port), Phase10Handler, rows=rows, output_dir=output,
        overwrite=False, max_upload_bytes=args.max_upload_mb * 1024 * 1024,
        mode=args.mode, run_id=args.run_id,
    )
    url = f"http://127.0.0.1:{args.port}/"
    print(f"PHASE10_CAPTURE_SERVER=ready | manifest={len(rows)} | target_clips=6 | mode={args.mode} | run_id={args.run_id}", flush=True)
    print("Bound to 127.0.0.1 only. No application, classifier, MQTT or ESP32 endpoints exposed.", flush=True)
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
