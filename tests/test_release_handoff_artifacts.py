from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_setup_scripts_have_the_environment_template_they_copy():
    template = ROOT / ".env.example"
    assert template.is_file()
    for script in (
        ROOT / "scripts" / "setup_windows.ps1",
        ROOT / "scripts" / "start_app.ps1",
        ROOT / "scripts" / "configure_local_assets.ps1",
    ):
        assert script.is_file()
        assert template.exists(), f"{script.relative_to(ROOT)} references a missing environment template"


def test_environment_template_preserves_locked_safe_defaults():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ISL_HOST=127.0.0.1" in text
    assert "ISL_CONFIDENCE_THRESHOLD=0.40" in text
    assert "ISL_MIN_HAND_RATIO=0.30" in text
    assert "ISL_IOT_ENABLED=false" in text
    assert "MQTT_PASSWORD=" in text
    assert "ISL_TARGET_FPS=" not in text


def test_test_client_dependency_is_the_imported_httpx_package():
    text = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    requirement_lines = [line.strip().lower() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert any(line.startswith("httpx>=") for line in requirement_lines)
    assert not any(line.startswith("httpx2") for line in requirement_lines)


def test_release_version_metadata_is_consistent():
    import json
    import re

    release_text = (ROOT / "RELEASE_VERSION.txt").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "Version: 1.0.0-rc3" in release_text
    assert manifest["version"] == "1.0.0-rc3"
    assert re.search(r'^version\s*=\s*"1\.0\.0rc3"$', pyproject, re.M)

    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from isl_recognition.webapp import create_app
    assert create_app().openapi()["info"]["version"] == "1.0.0-rc3"


def test_consolidated_stabilization_and_phase1_tools_are_packaged():
    required = [
        ROOT / "RUN_COMPLETE_ISL_STABILIZATION.ps1",
        ROOT / "scripts" / "run_phase1_browser_capture.ps1",
        ROOT / "scripts" / "browser_capture_protocol.py",
        ROOT / "scripts" / "serve_browser_validation_capture.py",
        ROOT / "scripts" / "browser_validation_capture.html",
        ROOT / "scripts" / "evaluate_browser_canvas_validation.py",
        ROOT / "scripts" / "verify_release_checksums.py",
        ROOT / "engineering" / "PHASE0_SOURCE_AUDIT.md",
        ROOT / "engineering" / "PHASE1_BROWSER_CANVAS_BENCHMARK_RUNBOOK.md",
    ]
    assert all(path.is_file() for path in required)
    runner = (ROOT / "RUN_COMPLETE_ISL_STABILIZATION.ps1").read_text(encoding="utf-8")
    capture_runner = (ROOT / "scripts" / "run_phase1_browser_capture.ps1").read_text(encoding="utf-8")
    assert "ISL_IOT_ENABLED" in runner and "pre_iot_lockdown" in runner
    assert "ISL_IOT_ENABLED" in capture_runner and "--open-browser" in capture_runner
    assert "run_mqtt_simulator.py" not in runner


def test_release_checksum_verifier_covers_env_example_and_detects_tampering(tmp_path):
    import hashlib
    import importlib.util
    import sys

    script = ROOT / "scripts" / "verify_release_checksums.py"
    spec = importlib.util.spec_from_file_location("verify_release_checksums_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    env_template = tmp_path / ".env.example"
    ordinary = tmp_path / "file.txt"
    env_template.write_text("ISL_IOT_ENABLED=false\n", encoding="utf-8")
    ordinary.write_text("stable\n", encoding="utf-8")
    rows = []
    for path in (env_template, ordinary):
        rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    manifest = tmp_path / "SHA256SUMS.txt"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert module.verify(tmp_path, manifest) == [".env.example", "file.txt"]

    ordinary.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        module.verify(tmp_path, manifest)
