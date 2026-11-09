from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_capture_default_and_limit_preserve_169_frame_contract():
    source = (ROOT / "src/isl_recognition/app_config.py").read_text(encoding="utf-8")
    webapp = (ROOT / "src/isl_recognition/webapp.py").read_text(encoding="utf-8")
    assert 'ISL_MAX_CAPTURE_SECONDS", "6.0"' in source
    assert "between 1 and 6 seconds" in source
    assert '"max_frames": 169' in webapp
    assert "preprocess_feature_sequence(sequence, max_frame_len=169, feature_dim=134)" in webapp


def test_live_preview_has_no_artificial_long_delay_and_uses_cache_busted_assets():
    js = (ROOT / "src/isl_recognition/static/landmark_diagnostics.js").read_text(encoding="utf-8")
    html = (ROOT / "src/isl_recognition/static/index.html").read_text(encoding="utf-8")
    # The normal ready-state preview remains responsive at 10 ms.
    # During recording, only the separate diagnostic overlay is throttled to 500 ms.
    assert (
        "const delay = Number.isFinite(delayOverride) ? Math.max(10, delayOverride) "
        ": (busyState === 'capturing' ? 500 : 10);"
    ) in js
    assert "Math.min(480, video.videoWidth)" in js
    assert "/static/landmark_diagnostics.js?v=" in html
    assert "/static/app.js?v=" in html


def test_reference_video_diagnostic_uses_real_extractor_and_official_shape():
    script = (ROOT / "scripts/diagnose_reference_video.py").read_text(encoding="utf-8")
    assert "MediaPipeFeatureExtractor" in script
    assert "INCLUDETransformerSmall" in script
    assert "max_frame_len=169, feature_dim=134" in script
    assert '"model_input_shape": list(padded.shape)' in script
