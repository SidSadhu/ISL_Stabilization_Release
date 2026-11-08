#!/usr/bin/env python3
"""Loopback-only server for Phase 8 callback-lifecycle factorial capture."""
from __future__ import annotations

import argparse
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

from serve_browser_validation_capture import CaptureServer, Handler as BaseHandler, read_and_verify_manifest  # noqa: E402

MODES = {"gate_sync", "ungate_sync", "gate_async", "ungate_async"}
DESCRIPTIONS = {
    "gate_sync": "39 ms minimum callback interval; synchronous toDataURL",
    "ungate_sync": "no callback interval gate; synchronous toDataURL",
    "gate_async": "39 ms minimum callback interval; asynchronous toBlob",
    "ungate_async": "no callback interval gate; asynchronous toBlob",
}


class Phase8CaptureServer(CaptureServer):
    def __init__(self, *args, capture_mode: str, **kwargs):
        self.capture_mode = capture_mode
        super().__init__(*args, **kwargs)

    def persist_index(self) -> None:
        super().persist_index()
        target = self.output_dir / "capture_index.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["capture_config"].update({
            "phase": 8,
            "phase8_mode": self.capture_mode,
            "phase8_mode_description": DESCRIPTIONS[self.capture_mode],
            "iot_enabled": False,
            "classifier_checkpoint_loaded": False,
            "production_code_modified": False,
        })
        temp = target.with_suffix(".json.phase8.tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, target)


class Phase8Handler(BaseHandler):
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            template = (SCRIPTS / "browser_validation_capture_phase8.html").read_text(encoding="utf-8")
            page = template.replace("__PHASE8_MODE__", json.dumps(self.server.capture_mode))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        super().do_GET()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--max-upload-mb", type=int, default=512)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 1 <= args.max_upload_mb <= 1024:
        parser.error("--max-upload-mb must be between 1 and 1024")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        parser.error("ISL_IOT_ENABLED is true; Phase 8 capture is blocked")

    rows = read_and_verify_manifest(args.manifest.expanduser().resolve(), args.repo.expanduser().resolve(), args.label_map.resolve())
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = Phase8CaptureServer(
        ("127.0.0.1", args.port), Phase8Handler, rows=rows, output_dir=output,
        overwrite=False, max_upload_bytes=args.max_upload_mb * 1024 * 1024, capture_mode=args.mode,
    )
    page_url = f"http://127.0.0.1:{args.port}/"
    print(f"PHASE8_CAPTURE_SERVER=ready | pinned_manifest={len(rows)} | target_clips=6 | mode={args.mode}")
    print("Bound to 127.0.0.1 only. No application, classifier, MQTT or ESP32 endpoints are exposed.")
    print(f"URL={page_url}")
    print(f"OUTPUT={output}")
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
    print(f"RECEIVED={snapshot['received_count']}/{snapshot['expected_count']}")
    return 0 if snapshot["received_count"] == 6 else 2


if __name__ == "__main__":
    raise SystemExit(main())
