# Copy this file to secrets.h and replace the values.
# secrets.h is ignored by Git; never commit your real credentials.
#define ISL_WIFI_SSID "YOUR_PHONE_HOTSPOT_SSID"
#define ISL_WIFI_PASSWORD "YOUR_PHONE_HOTSPOT_PASSWORD"

// Laptop's hotspot/Wi-Fi IPv4, previously 10.140.96.1.
// Verify with ipconfig after starting the hotspot.
#define ISL_MQTT_BROKER_IP_1 10
#define ISL_MQTT_BROKER_IP_2 140
#define ISL_MQTT_BROKER_IP_3 96
#define ISL_MQTT_BROKER_IP_4 1
#define ISL_MQTT_PORT 1883

// Set both to empty strings only if your local Mosquitto listener allows anonymous clients.
#define ISL_MQTT_USERNAME "YOUR_MQTT_USERNAME"
#define ISL_MQTT_PASSWORD "YOUR_MQTT_PASSWORD"
