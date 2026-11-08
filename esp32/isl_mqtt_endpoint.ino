/*
 * Safe MQTT endpoint for the ISL project.
 * Libraries: PubSubClient and ArduinoJson v6+.
 * Physical LED action is intentionally disabled until the exact board is identified.
 */
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>

const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST = "YOUR_MQTT_BROKER_HOST";
const int MQTT_PORT = 1883;
const char* MQTT_USERNAME = "";  // optional; use an authenticated broker in LAN deployment
const char* MQTT_PASSWORD = "";
const char* COMMAND_TOPIC = "isl/command/v1";
const char* ACK_TOPIC = "isl/ack/v1";

// Set only after checking the exact board, pinout, LED polarity and current-limiting resistor.
// -1 leaves the physical actuator disabled; the message is rejected rather than guessed.
#ifndef LED_GPIO
#define LED_GPIO -1
#endif
#ifndef LED_ON_LEVEL
#define LED_ON_LEVEL HIGH
#endif
#ifndef LED_OFF_LEVEL
#define LED_OFF_LEVEL LOW
#endif
const uint32_t LED_BLINK_MS = 150;
// Device-side allowlist must mirror the two labels selected in config/iot_label_map.json.
// Empty defaults intentionally reject all commands; never add labels before the owner selects them.
#ifndef ISL_IOT_LABEL_1
#define ISL_IOT_LABEL_1 ""
#endif
#ifndef ISL_IOT_COMMAND_1
#define ISL_IOT_COMMAND_1 ""
#endif
#ifndef ISL_IOT_LABEL_2
#define ISL_IOT_LABEL_2 ""
#endif
#ifndef ISL_IOT_COMMAND_2
#define ISL_IOT_COMMAND_2 ""
#endif
const double MIN_CONFIDENCE = 0.70; // Independent actuator gate; provisional, not statistically calibrated.
const int MAX_SEEN_IDS = 64;

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Preferences prefs;
String seenIds[MAX_SEEN_IDS];
String seenCaptureIds[MAX_SEEN_IDS];
int seenNext = 0;
bool clockValid = false;
uint32_t lastMqttAttemptMs = 0;
uint32_t lastWifiReconnectMs = 0;
const uint32_t MQTT_RETRY_INTERVAL_MS = 5000;
const uint32_t WIFI_RETRY_INTERVAL_MS = 5000;

String timestampUtc() {
  time_t now = time(nullptr);
  struct tm timeinfo;
  if (now < 1760000000 || !gmtime_r(&now, &timeinfo)) return String("UNSYNCED");
  char buffer[25];
  strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  return String(buffer);
}

void publishAck(const String& commandId, const char* status, const String& detail) {
  StaticJsonDocument<384> doc;
  doc["schema_version"] = "1.0";
  doc["command_id"] = commandId;
  doc["status"] = status; // acknowledged | rejected | duplicate
  doc["detail"] = detail;
  doc["timestamp_utc"] = timestampUtc();
  char buffer[384];
  size_t length = serializeJson(doc, buffer, sizeof(buffer));
  if (mqtt.connected()) mqtt.publish(ACK_TOPIC, (const uint8_t*)buffer, length, false);
  Serial.printf("ACK %s id=%s detail=%s\n", status, commandId.c_str(), detail.c_str());
}

bool validUuid(const String& id) {
  if (id.length() != 36) return false;
  for (unsigned int i = 0; i < id.length(); ++i) {
    const char c = id.charAt(i);
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (c != '-') return false;
    } else {
      const bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
      if (!hex) return false;
    }
  }
  return true;
}

bool seenBefore(const String& id) {
  for (int i = 0; i < MAX_SEEN_IDS; ++i) if (seenIds[i] == id && id.length() > 0) return true;
  return false;
}

bool captureSeenBefore(const String& captureId) {
  for (int i = 0; i < MAX_SEEN_IDS; ++i) if (seenCaptureIds[i] == captureId && captureId.length() > 0) return true;
  return false;
}

bool rememberIdBeforeAction(const String& id, const String& captureId) {
  // Persist both IDs before GPIO action: at most one action per command and capture.
  // Verify the values can be read back; fail closed if NVS cannot retain dedup state.
  const String idKey = String("id") + seenNext;
  const String captureKey = String("cap") + seenNext;
  if (prefs.putString(idKey.c_str(), id) == 0 || prefs.putString(captureKey.c_str(), captureId) == 0) return false;
  if (prefs.getString(idKey.c_str(), "") != id || prefs.getString(captureKey.c_str(), "") != captureId) return false;
  const int next = (seenNext + 1) % MAX_SEEN_IDS;
  prefs.putInt("next", next);
  if (prefs.getInt("next", -1) != next) return false;
  seenIds[seenNext] = id;
  seenCaptureIds[seenNext] = captureId;
  seenNext = next;
  return true;
}

bool configuredIoTAllowlist() {
  String label1 = String(ISL_IOT_LABEL_1);
  String label2 = String(ISL_IOT_LABEL_2);
  String command1 = String(ISL_IOT_COMMAND_1);
  String command2 = String(ISL_IOT_COMMAND_2);
  const bool command1Valid = command1 == "LED_BLINK" || command1 == "LED_BLINK_TWICE" || command1 == "LED_BLINK_THREE";
  const bool command2Valid = command2 == "LED_BLINK" || command2 == "LED_BLINK_TWICE" || command2 == "LED_BLINK_THREE";
  return label1.length() > 0 && label2.length() > 0 && label1 != label2 && command1Valid && command2Valid;
}

bool approvedSourceCommandPair(const String& label, const String& command) {
  if (!configuredIoTAllowlist()) return false;
  return (label == String(ISL_IOT_LABEL_1) && command == String(ISL_IOT_COMMAND_1)) ||
         (label == String(ISL_IOT_LABEL_2) && command == String(ISL_IOT_COMMAND_2));
}

bool validateMessage(JsonDocument& doc, String& reason) {
  if (!doc.is<JsonObject>()) { reason = "payload_not_object"; return false; }
  if (!doc["schema_version"].is<const char*>() || String((const char*)doc["schema_version"]) != "1.0") { reason = "unsupported_schema_version"; return false; }
  if (!doc["command_id"].is<const char*>() || !validUuid(String((const char*)doc["command_id"]))) { reason = "invalid_command_id"; return false; }
  if (!doc["capture_id"].is<const char*>() || String((const char*)doc["capture_id"]).length() == 0 || String((const char*)doc["capture_id"]).length() > 128) { reason = "invalid_capture_id"; return false; }
  if (!doc["command"].is<const char*>()) { reason = "command_not_allowlisted"; return false; }
  if (!doc["source_label"].is<const char*>()) { reason = "source_label_not_allowlisted"; return false; }
  if (!approvedSourceCommandPair(String((const char*)doc["source_label"]), String((const char*)doc["command"]))) { reason = "source_command_pair_not_allowlisted_or_unconfigured"; return false; }
  if (!doc["confidence"].is<double>()) { reason = "invalid_confidence"; return false; }
  double confidence = doc["confidence"].as<double>();
  if (!isfinite(confidence) || confidence < MIN_CONFIDENCE || confidence > 1.0) { reason = "confidence_out_of_range"; return false; }
  if (!doc["issued_at"].is<double>() || !doc["expires_at"].is<double>()) { reason = "invalid_timestamp_fields"; return false; }
  if (!clockValid || time(nullptr) < 1760000000) { reason = "device_clock_not_synchronised"; return false; }
  double issued = doc["issued_at"].as<double>();
  double expires = doc["expires_at"].as<double>();
  double nowEpoch = (double)time(nullptr);
  if (!isfinite(issued) || !isfinite(expires) || issued > nowEpoch + 5.0) { reason = "issued_in_future_or_invalid"; return false; }
  if (expires <= nowEpoch || expires <= issued || expires - issued > 30.0) { reason = "command_expired_or_invalid_ttl"; return false; }
  reason = "validated";
  return true;
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  if (String(topic) != COMMAND_TOPIC) return;
  StaticJsonDocument<768> doc;
  DeserializationError error = deserializeJson(doc, payload, length);
  String commandId = "";
  if (!error && doc["command_id"].is<const char*>()) commandId = String((const char*)doc["command_id"]);
  if (error) { publishAck(commandId, "rejected", "malformed_json"); return; }

  if (commandId.length() > 0 && seenBefore(commandId)) {
    publishAck(commandId, "duplicate", "command_id_already_processed");
    return;
  }
  if (!error && doc["capture_id"].is<const char*>() && captureSeenBefore(String((const char*)doc["capture_id"]))) {
    publishAck(commandId, "duplicate", "capture_id_already_processed");
    return;
  }

  String reason;
  if (!validateMessage(doc, reason)) {
    publishAck(commandId, "rejected", reason);
    return;
  }
  if (LED_GPIO < 0) {
    publishAck(commandId, "rejected", "LED_GPIO_not_configured_for_verified_board");
    return;
  }

  // Persist ID before action so a duplicate delivery/reboot does not intentionally repeat it.
  const String captureId = String((const char*)doc["capture_id"]);
  if (!rememberIdBeforeAction(commandId, captureId)) {
    publishAck(commandId, "rejected", "failed_to_persist_idempotency_state");
    return;
  }
  const String command = String((const char*)doc["command"]);
  const int blinkCount = command == "LED_BLINK" ? 1 : (command == "LED_BLINK_TWICE" ? 2 : 3);
  for (int blink = 0; blink < blinkCount; ++blink) {
    digitalWrite(LED_GPIO, LED_ON_LEVEL);
    delay(LED_BLINK_MS);
    digitalWrite(LED_GPIO, LED_OFF_LEVEL);
    if (blink + 1 < blinkCount) delay(LED_BLINK_MS);
  }
  publishAck(commandId, "acknowledged", "validated_and_led_blink_command_processed");
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 20000) delay(250);
  if (WiFi.status() != WL_CONNECTED) Serial.println("Wi-Fi unavailable; actuator remains inactive.");
}

void connectMqtt() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  lastMqttAttemptMs = millis();
  String clientId = "isl-esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  bool connected;
  if (strlen(MQTT_USERNAME) > 0) connected = mqtt.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD);
  else connected = mqtt.connect(clientId.c_str());
  if (connected) {
    mqtt.subscribe(COMMAND_TOPIC, 1);
    Serial.printf("MQTT connected; subscribed to %s\n", COMMAND_TOPIC);
  }
}

void setup() {
  Serial.begin(115200);
  if (LED_GPIO >= 0) { pinMode(LED_GPIO, OUTPUT); digitalWrite(LED_GPIO, LED_OFF_LEVEL); }
  prefs.begin("isl-safe", false);
  const int persistedNext = prefs.getInt("next", 0);
  // A corrupted/out-of-range NVS pointer must never become an invalid array index.
  seenNext = (persistedNext >= 0 && persistedNext < MAX_SEEN_IDS) ? persistedNext : 0;
  if (seenNext != persistedNext) prefs.putInt("next", seenNext);
  for (int i = 0; i < MAX_SEEN_IDS; ++i) {
    seenIds[i] = prefs.getString((String("id") + i).c_str(), "");
    seenCaptureIds[i] = prefs.getString((String("cap") + i).c_str(), "");
  }
  connectWifi();
  if (WiFi.status() == WL_CONNECTED) {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    uint32_t start = millis();
    while (time(nullptr) < 1760000000 && millis() - start < 15000) delay(250);
    clockValid = time(nullptr) >= 1760000000;
  }
  if (!clockValid) Serial.println("NTP time not synchronised; all actuator commands will be rejected.");
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(1024);
  connectMqtt();
  if (!configuredIoTAllowlist()) Serial.println("IoT allowlist is empty/invalid; all actuator commands will be rejected.");
  Serial.println("ISL MQTT endpoint initialised. Verify board/electrical safety before enabling LED_GPIO.");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiReconnectMs >= WIFI_RETRY_INTERVAL_MS) {
    lastWifiReconnectMs = millis();
    WiFi.reconnect();
  }
  if (!clockValid && time(nullptr) >= 1760000000) {
    clockValid = true;
    Serial.println("Device time synchronised; command expiry checks enabled.");
  }
  if (!mqtt.connected() && millis() - lastMqttAttemptMs >= MQTT_RETRY_INTERVAL_MS) connectMqtt();
  mqtt.loop();
  delay(5);
}
