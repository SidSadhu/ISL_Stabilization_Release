from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class MQTTConfig:
    host: str = os.getenv("MQTT_HOST", "localhost")
    port: int = int(os.getenv("MQTT_PORT", "1883"))
    topic: str = os.getenv("MQTT_TOPIC", "isl/stable_word")
    username: str | None = os.getenv("MQTT_USERNAME")
    password: str | None = os.getenv("MQTT_PASSWORD")


class OptionalMQTTPublisher:
    """MQTT transport only; it intentionally has no model/stabilization logic."""

    def __init__(self, config: MQTTConfig | None = None):
        self.config = config or MQTTConfig()
        self.client = None

    def connect(self) -> bool:
        try:
            import paho.mqtt.client as mqtt
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            if self.config.username:
                self.client.username_pw_set(self.config.username, self.config.password)
            self.client.connect(self.config.host, self.config.port, keepalive=30)
            self.client.loop_start()
            return True
        except Exception:
            self.client = None
            return False

    def publish(self, word: str) -> bool:
        if not word:
            return False
        if self.client is None and not self.connect():
            return False
        try:
            info = self.client.publish(self.config.topic, word, qos=0, retain=False)
            return info.rc == 0
        except Exception:
            return False

    def close(self) -> None:
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            finally:
                self.client = None
