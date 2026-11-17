from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.iot_protocol import load_label_commands
from isl_recognition.labels import load_label_map


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def main() -> int:
    settings = Settings()
    print("ISL project preflight")
    print(f"Project: {ROOT}")
    print(f"Python: {sys.version.split()[0]} ({platform.platform()})")
    for module in ("fastapi", "uvicorn", "numpy", "pandas", "cv2", "mediapipe", "torch", "transformers", "paho.mqtt.client"):
        print(f"{'OK' if module_available(module) else 'MISSING':7} {module}")
    print(f"AI4BHARAT_REPO configured: {bool(settings.ai4bharat_repo)}; exists: {bool(settings.ai4bharat_repo and settings.ai4bharat_repo.is_dir())}")
    print(f"ISL_CHECKPOINT configured: {bool(settings.checkpoint)}; exists: {bool(settings.checkpoint and settings.checkpoint.is_file())}")
    label = resolve_label_map(settings)
    print(f"Label map: {label if label and label.is_file() else 'MISSING'}")
    print(f"Base pre-IoT lockdown policy: {settings.pre_iot_lockdown} (must remain True)")
    print(f"Local hardware demo override: {settings.iot_hardware_demo_enabled}")
    print(f"Effective IoT lock active: {settings.iot_lock_active}")
    print(f"Recognition threshold: {settings.confidence_threshold:.2f} (uncalibrated until complete validation)")
    print(f"IoT minimum confidence: {settings.iot_min_confidence:.2f} (separate, provisional actuator gate)")
    try:
        labels = load_label_map(label) if label and label.is_file() else {}
        mappings = load_label_commands(settings.iot_label_map_path, labels) if labels else {}
        print(f"IoT mapping: {len(mappings)} configured label(s); two or three are supported by the local ESP8266 demo profile")
    except Exception as exc:
        print(f"IoT mapping: INVALID/FAIL-CLOSED ({type(exc).__name__}: {exc})")
    print(f"Effective IoT lock: {'ON' if settings.iot_lock_active else 'OFF'}; preflight does not load model weights, access the webcam, or test a live MQTT broker.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
