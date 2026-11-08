#!/usr/bin/env python3
"""Loopback-only server for six requestVideoFrameCallback ISL diagnostics.

Reuses the existing validated capture receiver and bundle protocol. Only the
served HTML page changes; saved captures go to a separate Phase 6 directory.
"""
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

from serve_browser_validation_capture import (  # noqa: E402
    CaptureServer,
    Handler as BaseHandler,
    read_and_verify_manifest,
)


class Phase6CaptureServer(CaptureServer):
    """Persist Phase 6-specific frame-callback provenance in the capture index."""

    def persist_index(self) -> None:
        super().persist_index()
        target = self.output_dir / "capture_index.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["capture_config"].update({
            "capture_sampling_mode": "requestVideoFrameCallback",
            "capture_timestamp_field": "metadata.mediaTime",
            "draw_synchronous_inside_callback": True,
            "capture_current_time_recorded_separately": True,
            "presented_frames_recorded": True,
        })
        temp = target.with_suffix(".json.rvfc.tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, target)


class Phase6Handler(BaseHandler):
    """Serve only the Phase 6 RVFC page; delegate APIs to the audited receiver."""

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            page = (SCRIPTS / "browser_validation_capture_rvfc.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
            return
        super().do_GET()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--max-upload-mb", type=int, default=512)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 1 <= args.max_upload_mb <= 1024:
        parser.error("--max-upload-mb must be between 1 and 1024")
    if os.getenv("ISL_IOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        parser.error("ISL_IOT_ENABLED is true; Phase 6 capture is blocked")

    rows = read_and_verify_manifest(
        args.manifest.expanduser().resolve(),
        args.repo.expanduser().resolve(),
        args.label_map.resolve(),
    )
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = Phase6CaptureServer(
        ("127.0.0.1", args.port),
        Phase6Handler,
        rows=rows,
        output_dir=output,
        overwrite=False,
        max_upload_bytes=args.max_upload_mb * 1024 * 1024,
    )
    page_url = f"http://127.0.0.1:{args.port}/"
    print(f"PHASE6_CAPTURE_SERVER=ready | pinned_manifest={len(rows)} | target_clips=6")
    print("Sampling=requestVideoFrameCallback; timestamp=metadata.mediaTime; drawImage occurs inside its callback.")
    print("Bound to 127.0.0.1 only. IoT/MQTT/application endpoints are not exposed.")
    print(f"URL={page_url}")
    print(f"OUTPUT={output}")
    print("Press Ctrl+C only to stop manually; the PowerShell orchestrator stops this process after all six captures.")
    if args.open_browser:
        threading.Timer(0.75, lambda: webbrowser.open(page_url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
