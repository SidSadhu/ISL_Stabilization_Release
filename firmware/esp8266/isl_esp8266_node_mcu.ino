/*
 * ISL INCLUDE-50 -> ESP8266 NodeMCU V3 hardware demo
 *
 * Commands:
 *   hello -> green LED + OLED "HELLO!" + short active-LOW buzzer beep
 *   time  -> yellow LED + live IST clock for 10 seconds
 *
 * MQTT contract: schema 1.0
 *   Subscribe: isl/command/v1 at QoS 1
 *   Publish:   isl/ack/v1 at QoS 0 (PubSubClient publish limitation)
 * The acknowledgement is application-level and correlated by command_id.
 *
 * Install with Arduino IDE:
 *   - ESP8266 board package (tested target: NodeMCU 1.0 / ESP-12E)
 *   - PubSubClient by Nick O'Leary
 *   - ArduinoJson 6.x (6.21.x recommended)
 *   - Adafruit GFX Library
 *   - Adafruit SSD1306
 *
 * Keep this broker local to your laptop/hotspot. Port 1883 is not TLS-encrypted.
 */

#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <time.h>
#include <coredecls.h>
#include <ctype.h>
#include <math.h>

// Copy secrets.example.h to secrets.h and fill it in locally.
// secrets.h is ignored by Git so your Wi-Fi/MQTT credentials are not committed.
#include "secrets.h"

const char* WIFI_SSID = ISL_WIFI_SSID;
const char* WIFI_PASSWORD = ISL_WIFI_PASSWORD;
IPAddress MQTT_BROKER(
    ISL_MQTT_BROKER_IP_1, ISL_MQTT_BROKER_IP_2,
    ISL_MQTT_BROKER_IP_3, ISL_MQTT_BROKER_IP_4);
const uint16_t MQTT_PORT = ISL_MQTT_PORT;
const char* MQTT_USERNAME = ISL_MQTT_USERNAME;
const char* MQTT_PASSWORD = ISL_MQTT_PASSWORD;

const char* COMMAND_TOPIC = "isl/command/v1";
const char* ACK_TOPIC = "isl/ack/v1";
const char* MQTT_CLIENT_ID = "isl-esp8266-node-01";

constexpr uint8_t OLED_SDA_PIN = D2;       // GPIO4
constexpr uint8_t OLED_SCL_PIN = D1;       // GPIO5
constexpr uint8_t GREEN_LED_PIN = D5;      // GPIO14
constexpr uint8_t YELLOW_LED_PIN = D6;     // GPIO12
constexpr uint8_t BUZZER_PIN = D7;         // GPIO13, active LOW
constexpr uint8_t OLED_ADDRESS = 0x3C;
constexpr uint32_t HELLO_ACTION_MS = 2200;
constexpr uint32_t HELLO_BEEP_MS = 140;
constexpr uint32_t TIME_ACTION_MS = 30000;  // Keep the live clock visible for 30 seconds
constexpr float MIN_IOT_CONFIDENCE = 0.30f;
constexpr uint32_t MAX_COMMAND_TTL_SECONDS = 30;
constexpr size_t MQTT_BUFFER_SIZE = 768;
constexpr size_t RECENT_ID_COUNT = 24;

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Adafruit_SSD1306 display(128, 64, &Wire, -1);

enum ActiveAction : uint8_t { ACTION_NONE, ACTION_HELLO, ACTION_TIME };
ActiveAction activeAction = ACTION_NONE;
uint32_t actionEndsAt = 0;
uint32_t buzzerEndsAt = 0;
uint32_t lastClockRefreshAt = 0;

String recentCommandIds[RECENT_ID_COUNT];
String recentCaptureIds[RECENT_ID_COUNT];
size_t recentWriteIndex = 0;

bool oledReady = false;

// A plausible Unix epoch is not proof of a successful NTP update. Require an
// actual SNTP callback after the current Wi-Fi connection before MQTT/commands.
volatile bool ntpSyncReceived = false;
bool ntpConfiguredForCurrentConnection = false;

void onSystemTimeSet(bool fromSntp) {
  if (!fromSntp) return;
  ntpSyncReceived = true;
  const time_t syncedEpoch = time(nullptr);
  Serial.printf("[NTP] SNTP update received; UTC epoch=%lu\n",
                static_cast<unsigned long>(syncedEpoch));
}

void configureNtpAfterWiFiConnect() {
  if (WiFi.status() != WL_CONNECTED || ntpConfiguredForCurrentConnection) return;

  ntpSyncReceived = false;
  configTime(19800, 0, "time.windows.com", "time.google.com", "pool.ntp.org");
  ntpConfiguredForCurrentConnection = true;
  Serial.println("[NTP] configured after Wi-Fi connection; waiting for actual SNTP update");
}

bool timeIsSynchronized() {
  return ntpSyncReceived && time(nullptr) > 1700000000;
}

void showLines(const String& line1, const String& line2 = "",
               const String& line3 = "") {
  if (!oledReady) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("ISL IoT / ESP8266");
  display.drawFastHLine(0, 12, 128, SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 22);
  display.println(line1);
  display.setTextSize(1);
  display.setCursor(0, 49);
  if (line2.length()) display.println(line2);
  if (line3.length()) display.println(line3);
  display.display();
}

void safeOutputs() {
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(YELLOW_LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, HIGH);  // active-LOW buzzer: HIGH = OFF
  activeAction = ACTION_NONE;
  buzzerEndsAt = 0;
}

void showReady() {
  safeOutputs();
  showLines("READY", "Waiting for ISL command");
}

void publishAck(const String& commandId, const char* status,
                const char* detail) {
  Serial.printf("[MQTT ACK] id=%s status=%s detail=%s\n",
                commandId.c_str(), status, detail);
  StaticJsonDocument<320> ack;
  ack["schema_version"] = "1.0";
  ack["command_id"] = commandId;
  ack["status"] = status;
  ack["detail"] = detail;

  char timestamp[25] = "time-not-synced";
  if (timeIsSynchronized()) {
    time_t nowUtc = time(nullptr);
    struct tm utcParts;
    gmtime_r(&nowUtc, &utcParts);
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utcParts);
  }
  ack["timestamp_utc"] = timestamp;

  char output[320];
  const size_t written = serializeJson(ack, output, sizeof(output));
  if (written > 0 && mqtt.connected()) {
    mqtt.publish(ACK_TOPIC, output, false);  // never retain acknowledgements
  }
}

bool isUuid(const String& value) {
  if (value.length() != 36) return false;
  for (size_t i = 0; i < value.length(); ++i) {
    const char ch = value[i];
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (ch != '-') return false;
    } else if (!isxdigit(static_cast<unsigned char>(ch))) {
      return false;
    }
  }
  return true;
}

bool alreadySeen(const String& commandId, const String& captureId) {
  for (size_t i = 0; i < RECENT_ID_COUNT; ++i) {
    if (recentCommandIds[i] == commandId || recentCaptureIds[i] == captureId) {
      return true;
    }
  }
  return false;
}

void rememberIds(const String& commandId, const String& captureId) {
  recentCommandIds[recentWriteIndex] = commandId;
  recentCaptureIds[recentWriteIndex] = captureId;
  recentWriteIndex = (recentWriteIndex + 1) % RECENT_ID_COUNT;
}

void startHelloAction() {
  safeOutputs();
  activeAction = ACTION_HELLO;
  const uint32_t actionStartedAt = millis();
  actionEndsAt = actionStartedAt + HELLO_ACTION_MS;
  buzzerEndsAt = actionStartedAt + HELLO_BEEP_MS;
  digitalWrite(GREEN_LED_PIN, HIGH);
  digitalWrite(YELLOW_LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  showLines("HELLO!", "Green LED + beep");
}

void refreshClockDisplay() {
  if (!timeIsSynchronized()) {
    showLines("TIME", "Waiting for NTP sync");
    return;
  }

  time_t nowUtc = time(nullptr);
  struct tm localParts;
  localtime_r(&nowUtc, &localParts);
  char clockText[16];
  strftime(clockText, sizeof(clockText), "%H:%M:%S", &localParts);

  // Use a larger centered 2x font for the 8-character clock on the 128x64 OLED.
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("CURRENT TIME");
  display.drawFastHLine(0, 12, 128, SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(16, 25);
  display.println(clockText);
  display.setTextSize(1);
  display.setCursor(34, 52);
  display.println("Asia/Kolkata");
  display.display();
}

void startTimeAction() {
  safeOutputs();
  activeAction = ACTION_TIME;
  actionEndsAt = millis() + TIME_ACTION_MS;
  lastClockRefreshAt = 0;
  digitalWrite(YELLOW_LED_PIN, HIGH);
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, HIGH);
  refreshClockDisplay();
}

void reject(const String& commandId, const char* reason) {
  publishAck(commandId, "rejected", reason);
}

void handleCommand(const byte* payload, unsigned int length) {
  Serial.printf("[MQTT] received command payload (%u bytes)\n", length);
  if (length == 0 || length > MQTT_BUFFER_SIZE) {
    reject("", "invalid_payload_size");
    return;
  }

  StaticJsonDocument<MQTT_BUFFER_SIZE> doc;
  DeserializationError parseError = deserializeJson(doc, payload, length);
  if (parseError) {
    reject("", "invalid_json");
    return;
  }

  const String commandId = doc["command_id"] | "";
  const String command = doc["command"] | "";
  const String sourceLabel = doc["source_label"] | "";
  const String captureId = doc["capture_id"] | "";

  if (doc["schema_version"] != "1.0") {
    reject(commandId, "unsupported_schema_version");
    return;
  }
  if (!isUuid(commandId)) {
    reject(commandId, "invalid_command_id");
    return;
  }
  if (!isUuid(captureId)) {
    reject(commandId, "invalid_capture_id");
    return;
  }
  if (!doc["confidence"].is<float>() &&
      !doc["confidence"].is<double>() &&
      !doc["confidence"].is<int>()) {
    reject(commandId, "invalid_confidence");
    return;
  }
  const double confidence = doc["confidence"].as<double>();
  if (!isfinite(confidence) || confidence < MIN_IOT_CONFIDENCE || confidence > 1.0) {
    reject(commandId, "confidence_out_of_range");
    return;
  }
  // Epoch timestamps must be JSON integers. Whole Unix seconds make expiry
  // comparisons exact across the backend and embedded JSON implementation.
  if (!doc["issued_at"].is<long>() && !doc["issued_at"].is<unsigned long>()) {
    reject(commandId, "invalid_issued_at");
    return;
  }
  if (!doc["expires_at"].is<long>() && !doc["expires_at"].is<unsigned long>()) {
    reject(commandId, "invalid_expires_at");
    return;
  }
  if (!timeIsSynchronized()) {
    reject(commandId, "device_clock_not_synchronized");
    return;
  }

  const uint32_t issuedAt = doc["issued_at"].as<uint32_t>();
  const uint32_t expiresAt = doc["expires_at"].as<uint32_t>();
  const uint32_t now = static_cast<uint32_t>(time(nullptr));
  Serial.printf("[TIME] issued=%lu expires=%lu device_now=%lu ttl=%lu\n",
                static_cast<unsigned long>(issuedAt),
                static_cast<unsigned long>(expiresAt),
                static_cast<unsigned long>(now),
                static_cast<unsigned long>(expiresAt - issuedAt));
  if (issuedAt > now && issuedAt - now > 5U) {
    reject(commandId, "invalid_or_expired_time_window");
    return;
  }
  if (expiresAt <= now || expiresAt <= issuedAt ||
      expiresAt - issuedAt > MAX_COMMAND_TTL_SECONDS) {
    reject(commandId, "invalid_or_expired_time_window");
    return;
  }

  const bool isHello = sourceLabel == "hello" && command == "HELLO_ACTION";
  const bool isTime = sourceLabel == "time" && command == "TIME_ACTION";
  if (!isHello && !isTime) {
    reject(commandId, "command_not_allowlisted");
    return;
  }
  if (alreadySeen(commandId, captureId)) {
    publishAck(commandId, "duplicate", "command_or_capture_already_processed");
    return;
  }
  if (!oledReady) {
    reject(commandId, "oled_not_ready");
    return;
  }
  if (activeAction != ACTION_NONE) {
    reject(commandId, "device_action_busy");
    return;
  }

  rememberIds(commandId, captureId);
  if (isHello) {
    startHelloAction();
    publishAck(commandId, "acknowledged", "hello_outputs_activated");
  } else {
    startTimeAction();
    publishAck(commandId, "acknowledged", "time_display_activated");
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  if (String(topic) != COMMAND_TOPIC) return;
  handleCommand(payload, length);
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("[Wi-Fi] connecting to SSID %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    delay(250);
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[Wi-Fi] connected; NodeMCU IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("[Wi-Fi] connection timed out; will retry");
  }
}

void connectMQTT() {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED || !timeIsSynchronized()) return;
  static uint32_t lastAttemptAt = 0;
  if (millis() - lastAttemptAt < 3000) return;
  lastAttemptAt = millis();

  bool connected = false;
  if (strlen(MQTT_USERNAME) > 0) {
    connected = mqtt.connect(MQTT_CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD);
  } else {
    connected = mqtt.connect(MQTT_CLIENT_ID);
  }
  if (connected) {
    mqtt.subscribe(COMMAND_TOPIC, 1);
    Serial.printf("[MQTT] connected to %s:%u; subscribed %s\n",
                  MQTT_BROKER.toString().c_str(), MQTT_PORT, COMMAND_TOPIC);
    showLines("MQTT READY", WiFi.localIP().toString());
  } else {
    Serial.printf("[MQTT] connect failed, state=%d\n", mqtt.state());
  }
}

void updateActions() {
  const uint32_t now = millis();
  if (buzzerEndsAt && static_cast<int32_t>(now - buzzerEndsAt) >= 0) {
    digitalWrite(BUZZER_PIN, HIGH);
    buzzerEndsAt = 0;
  }
  if (activeAction == ACTION_TIME && now - lastClockRefreshAt >= 250) {
    lastClockRefreshAt = now;
    refreshClockDisplay();
  }
  if (activeAction != ACTION_NONE &&
      static_cast<int32_t>(now - actionEndsAt) >= 0) {
    showReady();
  }
}

void setup() {
  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(YELLOW_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  safeOutputs();

  Serial.begin(115200);
  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);
  oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
  showLines("BOOTING", "Connecting Wi-Fi");

  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  settimeofday_cb(onSystemTimeSet);
  connectWiFi();

  // Start SNTP only after Wi-Fi is connected. Unix epoch stays UTC for protocol
  // validation; localtime() uses IST for the OLED clock.
  configureNtpAfterWiFiConnect();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(MQTT_BUFFER_SIZE);
  mqtt.setKeepAlive(30);
  showLines("SYNCING", "Waiting for NTP");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    safeOutputs();
    ntpSyncReceived = false;
    ntpConfiguredForCurrentConnection = false;
    connectWiFi();
  }

  if (WiFi.status() == WL_CONNECTED) {
    configureNtpAfterWiFiConnect();
    connectMQTT();
    if (mqtt.connected()) mqtt.loop();
  }

  updateActions();
  delay(5);
}
