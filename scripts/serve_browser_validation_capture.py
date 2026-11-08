#!/usr/bin/env python3
"""Serve a local-only browser Canvas capture probe for the official 77 val clips.

The page uses the same 640px canvas, JPEG 0.92, 40 ms timer, 6 s cap and 169-frame
limit as the audited production client. It uploads one clip at a time to this
loopback-only receiver. The receiver has no route to the application or MQTT.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from browser_capture_protocol import CaptureBundleError, normalize_video_path, parse_capture_payload
from isl_recognition.labels import load_label_map
from prepare_official_validation_manifest import load_official_validation_rows

EXPECTED_COMMIT = "df348f4a640060d4d39f02556d9f96851e8c46c7"
EXPECTED_VAL_COUNT = 77


def read_and_verify_manifest(manifest_path: Path, repo_path: Path, label_map_path: Path) -> list[dict[str, str]]:
    labels = set(load_label_map(label_map_path))
    official_rows, commit, digest = load_official_validation_rows(repo_path, labels)
    if commit != EXPECTED_COMMIT:
        raise ValueError(f"AI4Bharat HEAD mismatch: {commit}")
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"video_path", "label"}.issubset(set(reader.fieldnames or [])):
            raise ValueError("Manifest must contain video_path,label columns")
        supplied = list(reader)
    expected_pairs = {
        (normalize_video_path(row["video_path"]).casefold(), row["label"].strip())
        for row in official_rows
    }
    supplied_pairs = {
        (normalize_video_path(row.get("video_path", "")).casefold(), row.get("label", "").strip())
        for row in supplied
    }
    if len(supplied) != EXPECTED_VAL_COUNT or supplied_pairs != expected_pairs:
        raise ValueError("Manifest is not the exact pinned 77-item official validation split")
    for row in supplied:
        label = row.get("label", "").strip()
        if label not in labels:
            raise ValueError(f"Unknown INCLUDE-50 label in manifest: {label}")
        row["video_path"] = normalize_video_path(row["video_path"])
        row["label"] = label
        row["split"] = "val"
        row["source_repo_commit"] = commit
        row["source_val_list_sha256"] = digest
    return supplied


def safe_capture_id(index: int, row: dict[str, str]) -> str:
    stem = Path(row["video_path"]).stem
    raw = f"{index:03d}_{row['label']}_{stem}".lower()
    return re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler, *, rows, output_dir: Path, overwrite: bool, max_upload_bytes: int):
        super().__init__(server_address, handler)
        self.rows = rows
        self.row_by_path = {row["video_path"].casefold(): (i, row) for i, row in enumerate(rows)}
        self.output_dir = output_dir
        self.overwrite = overwrite
        self.max_upload_bytes = max_upload_bytes
        self.received: dict[str, dict] = {}
        self.lock = RLock()
        (output_dir / "clips").mkdir(parents=True, exist_ok=True)
        (output_dir / "frames").mkdir(parents=True, exist_ok=True)
        self._load_existing_index()

    def _load_existing_index(self) -> None:
        """Resume only captures whose saved container still validates against this manifest."""
        index_path = self.output_dir / "capture_index.json"
        if not index_path.is_file():
            return
        try:
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            records = index_data.get("received", [])
            if not isinstance(records, list):
                return
            for record in records:
                if not isinstance(record, dict):
                    continue
                key = normalize_video_path(record.get("video_path", "")).casefold()
                expected = self.row_by_path.get(key)
                if expected is None:
                    continue
                container_rel = Path(record.get("container", ""))
                if container_rel.is_absolute() or ".." in container_rel.parts:
                    continue
                container_path = (self.output_dir / container_rel).resolve()
                try:
                    container_path.relative_to(self.output_dir.resolve())
                except ValueError:
                    continue
                if not container_path.is_file():
                    continue
                parsed = parse_capture_payload(container_path.read_bytes(), expected[1])
                if parsed.sha256 != record.get("sha256"):
                    continue
                self.received[key] = record
        except Exception as exc:
            print(f"WARN: existing capture index could not be resumed ({type(exc).__name__}: {exc}); starting with an empty in-memory index.")

    def snapshot(self) -> dict:
        with self.lock:
            received = [dict(value) for value in self.received.values()]
        return {
            "expected_count": len(self.rows),
            "received_count": len(received),
            "overwrite_enabled": self.overwrite,
            "missing_paths": [row["video_path"] for row in self.rows if row["video_path"].casefold() not in self.received],
            "received": received,
        }

    def persist_index(self) -> None:
        snapshot = self.snapshot()
        target = self.output_dir / "capture_index.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps({
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "official_split_count": len(self.rows),
            "official_repo_commit": EXPECTED_COMMIT,
            "received_count": snapshot["received_count"],
            "missing_paths": snapshot["missing_paths"],
            "received": snapshot["received"],
            "capture_config": {
                "canvas_width": 640, "jpeg_quality": 0.92, "timer_interval_ms": 40,
                "max_capture_seconds": 6.0, "max_frames": 169, "diagnostic_mode": True,
                "iot_enabled": False, "iot_command_emission": False,
            },
        }, indent=2), encoding="utf-8")
        os.replace(temp, target)


class Handler(BaseHTTPRequestHandler):
    server: CaptureServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 - BaseHTTPRequestHandler API
        sys.stdout.write("[browser-capture] " + (fmt % args) + "\n")

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/":
            page = (SCRIPTS / "browser_validation_capture.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/api/manifest":
            rows = [{"video_path": r["video_path"], "label": r["label"]} for r in self.server.rows]
            self._json(200, {"expected_count": len(rows), "rows": rows})
        elif path == "/api/status":
            self._json(200, self.server.snapshot())
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path != "/api/capture":
            self._json(404, {"error": "not_found"})
            return
        length = self.headers.get("Content-Length")
        try:
            size = int(length or "0")
        except ValueError:
            self._json(400, {"error": "invalid_content_length"})
            return
        if size < 1 or size > self.server.max_upload_bytes:
            self._json(413, {"error": f"Upload size must be 1..{self.server.max_upload_bytes} bytes"})
            return
        payload = self.rfile.read(size)
        if len(payload) != size:
            self._json(400, {"error": "truncated_http_body"})
            return
        try:
            # Parse identity first to select the expected official row.
            import struct
            from browser_capture_protocol import MAGIC, PREFIX
            if len(payload) < 12 or payload[:8] != MAGIC:
                raise CaptureBundleError("Invalid browser capture magic/version")
            header_len = PREFIX.unpack_from(payload, 8)[0]
            raw_header = json.loads(payload[12:12 + header_len].decode("utf-8"))
            path = normalize_video_path(raw_header.get("case", {}).get("video_path", ""))
            match = self.server.row_by_path.get(path.casefold())
            if match is None:
                raise CaptureBundleError(f"Video path is not in the pinned validation manifest: {path}")
            index, expected_row = match
            parsed = parse_capture_payload(payload, expected_row)
            key = expected_row["video_path"].casefold()
            capture_id = safe_capture_id(index + 1, expected_row)
            clip_path = self.server.output_dir / "clips" / f"{capture_id}.islbrw"
            frame_dir = self.server.output_dir / "frames" / capture_id
            with self.server.lock:
                # Fail closed for both indexed captures and orphaned files left by an
                # interrupted prior write. --overwrite is the only replacement path.
                if not self.server.overwrite and (key in self.server.received or clip_path.exists()):
                    self._json(409, {"error": "duplicate_capture", "video_path": expected_row["video_path"]})
                    return
                temp_clip = clip_path.with_suffix(".islbrw.tmp")
                temp_clip.write_bytes(payload)
                os.replace(temp_clip, clip_path)
                frame_dir.mkdir(parents=True, exist_ok=True)
                for existing in frame_dir.glob("frame_*.jpg"):
                    existing.unlink()
                for frame_index, jpeg in enumerate(parsed.frames):
                    (frame_dir / f"frame_{frame_index:03d}.jpg").write_bytes(jpeg)
                case = parsed.header["case"]
                record = {
                    "capture_id": capture_id,
                    "video_path": expected_row["video_path"],
                    "label": expected_row["label"],
                    "frame_count": len(parsed.frames),
                    "sha256": parsed.sha256,
                    "container": str(clip_path.relative_to(self.server.output_dir)).replace("\\", "/"),
                    "frames_dir": str(frame_dir.relative_to(self.server.output_dir)).replace("\\", "/"),
                    "source_name": case["source_name"],
                    "browser_user_agent": case.get("browser_user_agent", ""),
                    "captured_at_utc": case.get("captured_at_utc", ""),
                    "duplicate_frame_skips": case["duplicate_frame_skips"],
                    "browser_dropped_video_frames": case["browser_dropped_video_frames"],
                }
                self.server.received[key] = record
                self.server.persist_index()
                received_count = len(self.server.received)
            self._json(200, {"status": "saved", **record, "received_count": received_count})
        except (CaptureBundleError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"invalid_capture: {exc}"})
        except Exception as exc:
            self._json(500, {"error": f"capture_write_failed: {type(exc).__name__}: {exc}"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="CSV from prepare_official_validation_manifest.py")
    parser.add_argument("--repo", type=Path, default=Path(os.getenv("AI4BHARAT_REPO", "")) if os.getenv("AI4BHARAT_REPO") else None,
                        help="Pinned AI4Bharat clone; defaults to AI4BHARAT_REPO")
    parser.add_argument("--label-map", type=Path, default=ROOT / "config" / "label_map_include50.json")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "browser_canvas_validation")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-upload-mb", type=int, default=512)
    parser.add_argument("--overwrite", action="store_true", help="Allow a later capture to replace a previously captured validation clip")
    parser.add_argument("--open-browser", action="store_true", help="Open the local capture page after the receiver binds")
    args = parser.parse_args()
    if args.repo is None:
        parser.error("--repo or AI4BHARAT_REPO is required")
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 1 <= args.max_upload_mb <= 1024:
        parser.error("--max-upload-mb must be between 1 and 1024")
    try:
        rows = read_and_verify_manifest(args.manifest.expanduser().resolve(), args.repo.expanduser().resolve(), args.label_map.resolve())
        output = args.output.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        server = CaptureServer(("127.0.0.1", args.port), Handler, rows=rows, output_dir=output,
                               overwrite=args.overwrite, max_upload_bytes=args.max_upload_mb * 1024 * 1024)
        print(f"Official validation manifest verified: {len(rows)} clips | AI4Bharat {EXPECTED_COMMIT}")
        print("IoT is not connected or callable from this diagnostic server. No app/MQTT endpoints are exposed.")
        page_url = f"http://127.0.0.1:{args.port}/"
        print(f"Open {page_url} in Chrome or Edge.")
        print(f"Output: {output}")
        print("Press Ctrl+C to stop the local receiver.")
        if args.open_browser:
            threading.Timer(0.75, lambda: webbrowser.open(page_url)).start()
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            print("\nStopping browser capture receiver.")
        finally:
            server.persist_index()
            server.server_close()
        snapshot = server.snapshot()
        print(f"Received {snapshot['received_count']}/{snapshot['expected_count']} official validation clips.")
        if snapshot["missing_paths"]:
            print("Missing captures:")
            for path in snapshot["missing_paths"]:
                print(f"  - {path}")
        return 0 if snapshot["received_count"] == snapshot["expected_count"] else 2
    except Exception as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
