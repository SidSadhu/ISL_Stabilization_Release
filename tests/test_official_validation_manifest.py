from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from isl_recognition.labels import load_label_map
import prepare_official_validation_manifest as manifest


def test_official_validation_parser_checks_provenance_and_labels(monkeypatch):
    raw = (
        b"Greetings/22. Hello/hello.MOV\n"
        b"Greetings/55. Thank you/thankyou.MOV\n"
    )
    monkeypatch.setattr(manifest, "OFFICIAL_VAL_LIST_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(manifest, "EXPECTED_VAL_COUNT", 2)
    labels = set(load_label_map(ROOT / "config" / "label_map_include50.json"))
    rows = manifest.parse_validation_list(raw, labels)
    assert [r["label"] for r in rows] == ["hello", "thankyou"]
    assert all(r["split"] == "val" for r in rows)


def test_official_validation_parser_rejects_path_traversal(monkeypatch):
    raw = b"../outside/22. Hello/hello.MOV\n"
    monkeypatch.setattr(manifest, "OFFICIAL_VAL_LIST_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(manifest, "EXPECTED_VAL_COUNT", 1)
    labels = set(load_label_map(ROOT / "config" / "label_map_include50.json"))
    try:
        manifest.parse_validation_list(raw, labels)
    except ValueError as exc:
        assert "Unsafe" in str(exc)
    else:
        raise AssertionError("Path traversal must be rejected")


def test_official_validation_parser_rejects_hash_mismatch(monkeypatch):
    monkeypatch.setattr(manifest, "OFFICIAL_VAL_LIST_SHA256", "0" * 64)
    try:
        manifest.parse_validation_list(b"Greetings/22. Hello/hello.MOV\n", {"hello"})
    except ValueError as exc:
        assert "SHA256 mismatch" in str(exc)
    else:
        raise AssertionError("A changed upstream split list must be rejected")


def test_evaluation_runner_locks_test_split_by_default():
    import subprocess
    import sys
    script = ROOT / 'scripts' / 'evaluate_dataset.py'
    result = subprocess.run(
        [sys.executable, str(script), '--manifest', 'does-not-need-to-exist.csv', '--split', 'test'],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert 'test split is locked' in result.stderr


def test_evaluation_requires_exact_path_and_label_pairs_for_official_validation():
    from evaluate_dataset import official_manifest_matches
    expected = [
        {"video_path": "Greetings/22. Hello/hello.MOV", "label": "hello"},
        {"video_path": "Greetings/55. Thank you/thankyou.MOV", "label": "thankyou"},
    ]
    assert official_manifest_matches(expected, expected)
    assert not official_manifest_matches(expected[:1], expected)
    assert not official_manifest_matches([
        {"video_path": "Greetings/22. Hello/hello.MOV", "label": "thankyou"},
        expected[1],
    ], expected)
