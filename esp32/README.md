# ESP32 MQTT Endpoint — firmware implemented, hardware not tested

The sketch `isl_mqtt_endpoint.ino` is designed to match [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md) MQTT protocol v1.0. It subscribes to `isl/command/v1`, accepts only the two compile-time label/command pairs configured by an owner-approved IoT release, validates the schema/UUID/confidence/expiry, suppresses duplicate command IDs and capture IDs using a bounded 64-entry `Preferences`/NVS ring, controls one configured LED GPIO for one/two/three blinks, and publishes `acknowledged`, `rejected` or `duplicate` to `isl/ack/v1`. The default allowlist is empty and the GPIO is disabled.

## Configure before compilation

- Install a supported ESP32 Arduino board package plus the `PubSubClient` and `ArduinoJson` libraries compatible with the sketch.
- Edit placeholders for Wi-Fi SSID/password, broker host/address, MQTT authentication and a verified GPIO only in your local working copy; never commit credentials.
- Set the exact board and the LED GPIO based on the board pinout. The sketch intentionally defaults to an invalid/unconfigured GPIO so a command cannot actuate a guessed pin.
- Confirm NTP/time is available so expiry is meaningful. Commands that cannot be validated against a valid clock must not actuate.
- The Mosquitto sample included here binds to loopback; it is suitable only for local software simulation. A physical ESP32 connects over the LAN, so configure a separate authenticated broker listener with topic ACLs and firewall restrictions; do not expose the anonymous sample to the LAN.

## Safety gate before applying power

No physical board/component set was confirmed in the implementation environment. Do **not** infer the sketch compiles or works because Python tests passed. Before connecting anything:

1. Identify the exact ESP32 board model/revision and confirm its GPIO electrical limits from authoritative board documentation.
2. Verify LED polarity and use a correctly sized current-limiting resistor; confirm the power supply and ground/wiring.
3. Compile the sketch for the chosen board and resolve any library/API errors.
4. First validate the topic/schema with the Python simulator and a broker, then with the board disconnected from any actuator.
5. Perform only one supervised low-voltage LED test after software validation; no relays, mains or higher-power loads.

The firmware acknowledgement says only that the controller validated/processed the command. It is not independent proof that a physical LED emitted light. The current project build did not compile this sketch or perform physical hardware tests.


## Current two-label firmware contract (not released for physical actuation)

The firmware no longer contains a default `hello` mapping. It rejects all actuator commands while the two compile-time pairs are empty. After an owner-approved IoT release, define these four macros in the build configuration to mirror the two entries in `config/iot_label_map.json`:

```cpp
#define ISL_IOT_LABEL_1 "<first official INCLUDE-50 label>"
#define ISL_IOT_COMMAND_1 "LED_BLINK"
#define ISL_IOT_LABEL_2 "<second official INCLUDE-50 label>"
#define ISL_IOT_COMMAND_2 "LED_BLINK_TWICE"
```

The example command values are illustrative only; the two labels and approved blink patterns must be deliberately selected, reviewed, and kept identical to the backend JSON. `LED_GPIO` remains `-1` by default, which keeps the physical output disabled. Do not enable it before the software-ready and electrical-safety gates are passed.


## Replay and delivery semantics

The firmware retains the most recent 64 command IDs and capture IDs across reboots and validates its stored ring pointer before using it. The backend keeps a separate SQLite capture-idempotency ledger that is not cleared by visible history reset or retention. This intentionally favours at-most-once actuation over automatic retry: a crash or publish/device failure after the backend reserves a capture may result in no physical action for that capture. Capture a new sign after resolving the failure; do not replay an old command to force an action. This is not an exactly-once delivery system.
