from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from test_webapp import make_services
from isl_recognition.webapp import create_app


def test_gallery_exposes_multiple_local_clips_and_their_original_splits(tmp_path):
    video_root = tmp_path / "videos"
    clips = [
        ("Greetings/22. Hello/MVI_train.mov", "train", b"train-video"),
        ("Greetings/22. Hello/MVI_val.mov", "val", b"val-video"),
        ("Greetings/22. Hello/MVI_test.mov", "test", b"test-video"),
    ]
    for relative, _split, body in clips:
        file_path = video_root / relative
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(body)
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"videos": [
        {"parent_label": "Greetings", "label": "Greetings/22. Hello", "video_path": relative,
         "include_50": True, "split": split}
        for relative, split, _body in clips
    ]}), encoding="utf-8")

    services = make_services(tmp_path)
    services.settings = replace(services.settings, reference_video_dir=video_root, reference_metadata_path=metadata_path)
    with TestClient(create_app(services)) as client:
        gallery = client.get("/api/reference-videos").json()
        hello = next(item for item in gallery["classes"] if item["label"] == "hello")
        assert hello["available_count"] == 3
        assert [item["split"] for item in hello["videos"]] == ["train", "val", "test"]
        assert [client.get(item["url"]).content for item in hello["videos"]] == [b"train-video", b"val-video", b"test-video"]


def test_gallery_html_and_javascript_expose_clip_selector():
    project_root = Path(__file__).resolve().parents[1]
    html = (project_root / "src" / "isl_recognition" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (project_root / "src" / "isl_recognition" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="referenceClipSelect"' in html
    assert 'id="referenceClipCount"' in html
    assert "selector.onchange" in app_js
    assert "video.split" in app_js
