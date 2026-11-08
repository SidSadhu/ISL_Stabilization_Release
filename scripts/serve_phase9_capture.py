#!/usr/bin/env python3
"""Loopback-only Phase 9 receiver for repeated first-frame capture runs."""
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


class Phase9CaptureServer(CaptureServer):
    def __init__(self, *args, encoder: str, repeat: int, run_id: str, **kwargs):
        self.encoder = encoder
        self.repeat = repeat
        self.run_id = run_id
        super().__init__(*args, **kwargs)

    def persist_index(self) -> None:
        super().persist_index()
        target = self.output_dir / "capture_index.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["capture_config"].update({
            "phase": 9,
            "phase9_encoder": self.encoder,
            "phase9_repeat": self.repeat,
            "phase9_run_id": self.run_id,
            "phase9_initial_frame_policy": "paused_draw_encode_before_play",
            "phase9_callback_gate_ms": 0,
            "iot_enabled": False,
            "classifier_checkpoint_loaded": False,
            "production_code_modified": False,
        })
        temporary = target.with_suffix(".json.phase9.tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temporary, target)


class Phase9Handler(BaseHandler):
    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/":
            template = (SCRIPTS / "browser_validation_capture_phase9.html").read_text(encoding="utf-8")
            page = template.replace("__PHASE9_ENCODER__", json.dumps(self.server.encoder))
            page = page.replace("__PHASE9_REPEAT__", str(self.server.repeat))
            page = page.replace("__PHASE9_RUN_ID__", json.dumps(self.server.run_id))
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
                self._json(413, {"error": "invalid_phase9_upload_length"})
                return
            payload = self.rfile.read(size)
            if len(payload) != size or payload[:8] != MAGIC:
                self._json(400, {"error": "invalid_phase9_capture_payload"})
                return
            header_length = PREFIX.unpack_from(payload, 8)[0]
            if header_length < 2 or 12 + header_length > len(payload):
                self._json(400, {"error": "invalid_phase9_header_length"})
                return
            header = json.loads(payload[12:12 + header_length].decode("utf-8"))
            case = header.get("case", {})
            config = header.get("capture_config", {})
            expected = (self.server.encoder, self.server.repeat, self.server.run_id)
            case_identity = (case.get("phase9_encoder"), case.get("phase9_repeat"), case.get("phase9_run_id"))
            config_identity = (config.get("phase9_encoder"), config.get("phase9_repeat"), config.get("phase9_run_id"))
            if case_identity != expected or config_identity != expected:
                self._json(400, {"error": "phase9_run_identity_mismatch", "expected": expected,
                                 "case_identity": case_identity, "config_identity": config_identity})
                return
            if case.get("phase9_initial_frame_policy") != "paused_draw_encode_before_play":
                self._json(400, {"error": "phase9_initial_frame_policy_mismatch"})
                return
            # The shared receiver performs the established manifest, JPEG and binary-protocol checks.
            self.rfile = io.BytesIO(payload)
            super().do_POST()
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"invalid_phase9_identity_header: {exc}"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoder", choices=("sync", "async"), required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--max-upload-mb", type=int, default=512)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 1 <= args.max_upload_mb <= 1024:
        parser.error("--max-upload-mb must be between 1 and 1024")
    if not args.run_id or len(args.run_id) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.run_id):
        parser.error("--run-id must contain only letters, numbers, underscore or hyphen (max 80 chars)")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        parser.error("ISL_IOT_ENABLED is true; Phase 9 capture is blocked")

    rows = read_and_verify_manifest(args.manifest.expanduser().resolve(), args.repo.expanduser().resolve(), args.label_map.resolve())
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = Phase9CaptureServer(
        ("127.0.0.1", args.port), Phase9Handler, rows=rows, output_dir=output,
        overwrite=False, max_upload_bytes=args.max_upload_mb * 1024 * 1024,
        encoder=args.encoder, repeat=args.repeat, run_id=args.run_id,
    )
    page_url = f"http://127.0.0.1:{args.port}/"
    print(f"PHASE9_CAPTURE_SERVER=ready | pinned_manifest={len(rows)} | target_clips=6 | encoder={args.encoder} | repeat={args.repeat} | run_id={args.run_id}", flush=True)
    print("Bound to 127.0.0.1 only. No application, classifier, MQTT or ESP32 endpoints are exposed.", flush=True)
    print(f"URL={page_url}", flush=True)
    print(f"OUTPUT={output}", flush=True)
    if args.open_browser:
        threading.Timer(0.75, lambda: webbrowser.open(page_url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.persist_index()
        server.server_close()
    snapshot = server.snapshot()
    print(f"RECEIVED={snapshot['received_count']}/{snapshot['expected_count']}", flush=True)
    return 0 if snapshot["received_count"] == 6 else 2


if __name__ == "__main__":
    raise SystemExit(main())
