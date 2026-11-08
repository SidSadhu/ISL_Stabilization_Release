from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase6_launcher_temporarily_continues_for_native_stderr():
    source = (ROOT / "scripts" / "run_phase6_rvfc_alignment.ps1").read_text(encoding="utf-8-sig")
    assert "$ErrorActionPreference = 'Continue'" in source
    assert "$analysisExit = $LASTEXITCODE" in source
    assert "$previousErrorActionPreference" in source
    assert "finally" in source


def test_saved_capture_recovery_does_not_request_browser_recapture():
    source = (ROOT / "scripts" / "complete_phase6_from_saved_capture.ps1").read_text(encoding="utf-8-sig")
    assert "browser_canvas_validation_phase6_rvfc_*" in source
    assert "capture_index.json" in source
    assert "exactly six" in source or "-ne 6" in source
    assert "complete_phase6_from_saved_capture" not in source  # no recursion
    assert "--capture-dir $CapturePath" in source
    assert "PHASE6_RESULT_BUNDLE_" in source


def test_saved_capture_recovery_preserves_iot_lockdown():
    source = (ROOT / "scripts" / "complete_phase6_from_saved_capture.ps1").read_text(encoding="utf-8-sig")
    assert "$env:ISL_IOT_ENABLED = 'false'" in source
    assert "mappings.PSObject.Properties" in source
    assert "pre_iot_lockdown" in source
    assert "classifier_checkpoint_loaded=false" in source


def test_wildcard_capture_copy_uses_get_childitem_not_literalpath():
    for name in ("run_phase6_rvfc_alignment.ps1", "complete_phase6_from_saved_capture.ps1"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8-sig")
        assert "Get-ChildItem -LiteralPath (Join-Path $CapturePath 'clips') -Filter '*.islbrw' -File | Copy-Item" in source
        assert "-LiteralPath (Join-Path $CapturePath 'clips\\*.islbrw')" not in source
