from __future__ import annotations

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_include_reference_clips.py"
spec = importlib.util.spec_from_file_location("include_reference_downloader", SCRIPT)
downloader = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(downloader)


def row(label: str, split: str, path: str) -> dict:
    return {"model_label": label, "split": split, "video_path": path, "label": label, "parent_label": "test"}


def test_coverage_selects_only_missing_classes_and_prefers_train(tmp_path):
    existing = tmp_path / "Greetings" / "22. Hello" / "hello_train.mov"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    rows = [
        row("hello", "train", "Greetings/22. Hello/hello_train.mov"),
        row("hello", "val", "Greetings/22. Hello/hello_val.mov"),
        row("good", "test", "Greetings/18. Good/good_test.mov"),
        row("good", "train", "Greetings/18. Good/good_train.mov"),
    ]
    selected = downloader.select_rows(rows, tmp_path, "coverage")
    assert [(item["model_label"], item["split"]) for item in selected] == [("good", "train")]


def test_validation_all_selects_every_missing_val_row_and_never_test(tmp_path):
    rows = [
        row("hello", "train", "Greetings/22. Hello/hello_train.mov"),
        row("hello", "val", "Greetings/22. Hello/hello_val.mov"),
        row("hello", "test", "Greetings/22. Hello/hello_test.mov"),
        row("black", "val", "Colours/black_val.mov"),
        row("black", "test", "Colours/black_test.mov"),
        row("good", "val", "Greetings/good_val.mov"),
        row("good", "test", "Greetings/good_test.mov"),
    ]
    selected = downloader.select_rows(rows, tmp_path, "validation-all")
    assert {(item["model_label"], item["split"]) for item in selected} == {
        ("hello", "val"), ("black", "val"), ("good", "val")
    }


def test_canonical_label_handles_official_numbered_glosses():
    valid = {"hello", "goodmorning", "thankyou", "tshirt"}
    assert downloader.canonical_label("Greetings/22. Hello", valid) == "hello"
    assert downloader.canonical_label("Greetings/55. Thank you", valid) == "thankyou"
    assert downloader.canonical_label("Greetings/19. Good morning", valid) == "goodmorning"
    assert downloader.canonical_label("Clothes/3. T-shirt", valid) == "tshirt"


def test_range_reader_rejects_server_that_ignores_range():
    payload = b"0123456789"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reader = downloader.HTTPRangeReader(f"http://127.0.0.1:{server.server_port}/archive.zip", len(payload))
        with pytest.raises(downloader.RangeNotSupported, match="did not honour"):
            reader.read(4)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_range_reader_reads_exact_range():
    payload = b"abcdefghij"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            header = self.headers.get("Range", "")
            start_end = header.removeprefix("bytes=").split("-")
            start, end = int(start_end[0]), int(start_end[1])
            chunk = payload[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reader = downloader.HTTPRangeReader(f"http://127.0.0.1:{server.server_port}/archive.zip", len(payload))
        assert reader.read(3) == b"abc"
        assert reader.tell() == 3
        reader.seek(5)
        assert reader.read(2) == b"fg"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_remote_zip_index_and_member_download_use_ranges_only(tmp_path):
    import io
    import zipfile

    data = io.BytesIO()
    member_name = "archive-root/Greetings/22. Hello/hello.mov"
    video_bytes = b"sample-video-content-for-range-test"
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
        archive_file.writestr(member_name, video_bytes)
    archive_bytes = data.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            header = self.headers.get("Range", "")
            if not header.startswith("bytes="):
                self.send_response(200)
                self.end_headers()
                return
            start_end = header.removeprefix("bytes=").split("-")
            start, end = int(start_end[0]), int(start_end[1])
            chunk = archive_bytes[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(archive_bytes)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote_archive = {"key": "Greetings.zip", "url": f"http://127.0.0.1:{server.server_port}/Greetings.zip", "size": len(archive_bytes)}
        wanted = {"greetings/22. hello/hello.mov"}
        found = downloader.index_remote_archives([remote_archive], wanted)
        assert wanted.issubset(found)
        output_root = tmp_path / "videos"
        size = downloader.download_member(remote_archive, found[next(iter(wanted))][1], output_root, "Greetings/22. Hello/hello.mov")
        assert size == len(video_bytes)
        assert (output_root / "Greetings" / "22. Hello" / "hello.mov").read_bytes() == video_bytes
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_remote_mp4_member_matches_official_mov_path_without_renaming(tmp_path):
    import io
    import zipfile

    data = io.BytesIO()
    member_name = "Animals/1. Dog/MVI_8561.MP4"
    video_bytes = b"verified-mp4-container-bytes"
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
        archive_file.writestr(member_name, video_bytes)
    archive_bytes = data.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            header = self.headers.get("Range", "")
            if not header.startswith("bytes="):
                self.send_response(200)
                self.end_headers()
                return
            start_end = header.removeprefix("bytes=").split("-")
            start, end = int(start_end[0]), int(start_end[1])
            chunk = archive_bytes[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(archive_bytes)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote_archive = {"key": "Animals_1of2.zip", "url": f"http://127.0.0.1:{server.server_port}/Animals.zip", "size": len(archive_bytes)}
        official_path = "animals/1. dog/mvi_8561.mov"
        found = downloader.index_remote_archives([remote_archive], {official_path})
        assert official_path in found
        actual_member = found[official_path][1]
        assert actual_member == member_name
        local_relative = downloader.archive_member_target_path(
            "Animals/1. Dog/MVI_8561.MOV", actual_member
        )
        assert local_relative == "Animals/1. Dog/MVI_8561.MP4"
        # Archive-root prefixes are stripped, but the manifest folder/stem is retained.
        assert downloader.archive_member_target_path(
            "Animals/1. Dog/MVI_8561.MOV", "INCLUDE/Animals/1. Dog/MVI_8561.MP4"
        ) == "Animals/1. Dog/MVI_8561.MP4"

        output_root = tmp_path / "videos"
        size = downloader.download_member(remote_archive, actual_member, output_root, local_relative)
        assert size == len(video_bytes)
        actual_path = output_root / "Animals" / "1. Dog" / "MVI_8561.MP4"
        assert actual_path.read_bytes() == video_bytes
        assert not (output_root / "Animals" / "1. Dog" / "MVI_8561.MOV").exists()

        resolved = downloader.local_video_path(output_root, row("dog", "val", "Animals/1. Dog/MVI_8561.MOV"))
        assert resolved == actual_path
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_local_video_path_rejects_ambiguous_same_stem_extensions(tmp_path):
    directory = tmp_path / "Home" / "28. Window"
    directory.mkdir(parents=True)
    (directory / "MVI_9013.MP4").write_bytes(b"mp4")
    (directory / "MVI_9013.M4V").write_bytes(b"m4v")
    with pytest.raises(ValueError, match="ambiguous"):
        downloader.local_video_path(tmp_path, row("window", "val", "Home/28. Window/MVI_9013.MOV"))
