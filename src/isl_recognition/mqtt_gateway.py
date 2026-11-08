from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable


class MQTTGateway:
    """Optional Paho MQTT publisher/listener. MQTT failure never blocks recognition."""

    def __init__(
        self,
        settings,
        on_ack: Callable[[str, str, str | None], None] | None = None,
        on_publish_attempt: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.on_ack = on_ack
        self.on_publish_attempt = on_publish_attempt
        self.client = None
        self.lock = threading.RLock()
        self.connected = threading.Event()
        self.last_error: str | None = None

    def _connect(self) -> bool:
        # Reuse one Paho client so repeated failed/disconnected publishes do not leak
        # background network loops or create duplicate subscribers.
        with self.lock:
            existing = self.client
            if existing is not None and self.connected.is_set():
                return True
            if existing is None:
                try:
                    import paho.mqtt.client as mqtt
                    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="isl-web-backend")
                    if self.settings.mqtt_username:
                        client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
                    client.on_connect = self._on_connect
                    client.on_disconnect = self._on_disconnect
                    client.on_message = self._on_message
                    self.connected.clear()
                    client.connect(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
                    self.client = client
                    client.loop_start()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self.client = None
                    self.connected.clear()
                    return False
        # If an existing client is disconnected, Paho's network loop manages reconnects.
        # Wait briefly; never spawn another client just because a reconnect is still pending.
        if not self.connected.wait(timeout=2.0):
            self.last_error = self.last_error or "MQTT connection did not become ready within 2 seconds"
            return False
        return True

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if getattr(reason_code, "is_failure", False):
            self.last_error = f"MQTT connect rejected: {reason_code}"
            self.connected.clear()
            return
        self.connected.set()
        try:
            client.subscribe(self.settings.mqtt_ack_topic, qos=1)
        except Exception as exc:
            self.last_error = f"ack subscription failed: {exc}"

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected.clear()
        if reason_code:
            self.last_error = f"MQTT disconnected: {reason_code}"

    def _on_message(self, client, userdata, message) -> None:
        try:
            if message.topic != self.settings.mqtt_ack_topic:
                return
            ack = json.loads(message.payload.decode("utf-8"))
            if not isinstance(ack, dict) or ack.get("schema_version") != "1.0":
                return
            command_id = str(ack.get("command_id", ""))
            uuid.UUID(command_id)
            status = str(ack.get("status", ""))
            if status in {"acknowledged", "rejected", "duplicate"} and self.on_ack:
                self.on_ack(command_id, status, str(ack.get("detail", ""))[:300])
        except Exception:
            return

    def publish_command(self, payload: dict[str, Any]) -> tuple[bool, str]:
        if not self._connect():
            return False, self.last_error or "MQTT broker unavailable"
        try:
            if self.on_publish_attempt:
                self.on_publish_attempt(str(payload["command_id"]))
            info = self.client.publish(
                self.settings.mqtt_command_topic,
                json.dumps(payload, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            if info.rc != 0:
                return False, f"MQTT publish returned rc={info.rc}"
            info.wait_for_publish(timeout=2.0)
            if not info.is_published():
                return False, "MQTT publish acknowledgement timed out"
            # Broker acceptance is not proof that the subscriber performed the action.
            return True, "broker_publish_confirmed_waiting_for_subscriber_ack"
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False, self.last_error

    def close(self) -> None:
        with self.lock:
            client, self.client = self.client, None
            self.connected.clear()
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
            try:
                client.loop_stop()
            except Exception:
                pass
