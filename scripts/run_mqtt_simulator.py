from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from isl_recognition.app_config import Settings, resolve_label_map
from isl_recognition.iot_protocol import SimulatedESP32, load_label_commands
from isl_recognition.labels import load_label_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated ESP32 subscriber. This never touches physical hardware.")
    parser.add_argument("--host", default=os.getenv("MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--command-topic", default=os.getenv("MQTT_COMMAND_TOPIC", "isl/command/v1"))
    parser.add_argument("--ack-topic", default=os.getenv("MQTT_ACK_TOPIC", "isl/ack/v1"))
    parser.add_argument("--iot-map", type=Path, default=None, help="Optional JSON override for config/iot_label_map.json")
    args = parser.parse_args()
    settings = Settings()
    label_path = resolve_label_map(settings)
    if not label_path or not label_path.is_file():
        print("ERROR: Official INCLUDE-50 label map unavailable; simulator will not start.", file=sys.stderr)
        return 2
    try:
        labels = load_label_map(label_path)
        mapping = load_label_commands(args.iot_map or settings.iot_label_map_path, labels)
    except Exception as exc:
        print(f"ERROR: IoT mapping configuration is invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if len(mapping) != 2:
        print("ERROR: Configure exactly two IoT demonstration signs before starting the simulator. The default map is intentionally empty.", file=sys.stderr)
        return 2
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("ERROR: paho-mqtt is not installed. Run: python -m pip install -r requirements-mqtt.txt", file=sys.stderr)
        return 2

    simulator = SimulatedESP32(mapping, min_confidence=settings.iot_min_confidence)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="isl-simulated-esp32")
    username = os.getenv("MQTT_USERNAME")
    if username:
        client.username_pw_set(username, os.getenv("MQTT_PASSWORD", ""))

    def on_connect(client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "is_failure", False):
            print(f"MQTT connect rejected: {reason_code}", flush=True)
            return
        client.subscribe(args.command_topic, qos=1)
        print(f"SIMULATOR CONNECTED host={args.host}:{args.port} topic={args.command_topic}; allowlisted={sorted(mapping)}; min_confidence={settings.iot_min_confidence:.2f}", flush=True)

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            payload = None
        ack = simulator.process(payload)
        if ack["status"] == "acknowledged":
            print(f"SIMULATED LED BLINK id={ack['command_id']} (no physical GPIO touched)", flush=True)
        else:
            print(f"SIMULATOR {ack['status'].upper()} id={ack['command_id']} detail={ack['detail']}", flush=True)
        client.publish(args.ack_topic, json.dumps(ack, separators=(",", ":")), qos=1, retain=False)

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(args.host, args.port, keepalive=30)
        client.loop_forever()
    except KeyboardInterrupt:
        print("Simulator stopped.")
    except Exception as exc:
        print(f"ERROR: Cannot connect to MQTT broker: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
