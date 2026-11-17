# ESP8266 Local IoT Demonstration

This document describes the optional, experimental local hardware demonstration added on
`feature/esp8266-isl-iot-demo`. It does not change the INCLUDE-50 recognition model,
checkpoint, class order, landmark extractor, 134-D feature layout, 169-frame input,
or manual capture workflow.

The general recognition release remains a separate release-readiness decision. This
demonstration is explicitly opt-in and is not production certification.

## 1. Gesture behaviour

| INCLUDE-50 label | MQTT command | ESP8266 response |
|---|---|---|
| `quiet` | `QUIET_ACTION` | Green LED and OLED `QUIET!` remain active for 60 s; the directly connected 2-pin buzzer on D7 repeats 10 s ON / 10 s OFF for the duration of the action |
| `time` | `TIME_ACTION` | Yellow LED and large live 24-hour IST clock remain visible for 60 s |
| `i` | `WELCOME_ACTION` | Both LEDs stay ON; buzzer alternates 1 s ON / 1 s OFF; OLED shows only `Welcome`, `Dr. Arpan Shah`, `Sir` for 60 s |
| `hello` | none | Text recognition only; no MQTT publication |

The base `config/iot_label_map.json` remains empty for release compatibility.
This feature branch adds a separate `config/iot_label_map.esp8266.json` profile
containing `quiet`, `time` and `i`. The new `i` mapping triggers a 60-second welcome action. The mapping is independent of classifier output
ordering. `hello` is deliberately not mapped.

Both recognition and IoT confidence thresholds are 0.30 (30%) for this local demo
(`ISL_CONFIDENCE_THRESHOLD=0.30` and `ISL_IOT_MIN_CONFIDENCE=0.30`). Both are
provisional and uncalibrated; a score is not a guaranteed probability of correctness.
IoT actuation additionally requires an expected INCLUDE-50 label selected before
capture, an exact match to the backend prediction, an accepted result, and an
approved mapping. Missing expected labels and mismatches always suppress actuation.
Text recognition remains available when IoT is off or a label is not mapped.

## 2. Default-off safety and explicit opt-in

The backend's pre-IoT lock remains ON by default. The process-local IoT enable flag
also starts OFF on every application launch. Both protections must be addressed:

1. Set `ISL_IOT_HARDWARE_DEMO=true` and
   `ISL_IOT_LABEL_MAP=config/iot_label_map.esp8266.json` in the local `.env`
   only for the local demo.
2. Select the expected INCLUDE-50 sign in the reference gallery and press
   `Use as expected sign` before arming capture. The backend validates the label
   and freezes it into the capture at START.
3. Start the backend.
4. Confirm `GET /api/health` reports `pre_iot_lockdown: false`,
   `iot_mapping_count: 3`, `iot_expected_sign_match_required: true`, and no
   `iot_mapping_error`.
5. Enable IoT using the existing UI toggle. It is session-local and starts OFF again
   after restarting the application.

If the demo flag is absent, malformed, or set to a value other than `true`,
the pre-IoT lock remains active. The underlying `pre_iot_lockdown: bool = True`
release policy remains unchanged; the new profile exposes a separate effective-lock
property for this explicitly opted-in local demonstration.

Do not set this option on an internet-facing or production deployment. The backend
continues to bind to loopback only.

## 3. MQTT broker configuration

Backend (running on the same laptop as Mosquitto):

```dotenv
ISL_IOT_HARDWARE_DEMO=true
ISL_IOT_LABEL_MAP=config/iot_label_map.esp8266.json
ISL_CONFIDENCE_THRESHOLD=0.30
ISL_IOT_MIN_CONFIDENCE=0.30
MQTT_HOST=10.140.96.1
MQTT_PORT=1883
MQTT_COMMAND_TOPIC=isl/command/v1
MQTT_ACK_TOPIC=isl/ack/v1
MQTT_USERNAME=YOUR_MQTT_USERNAME
MQTT_PASSWORD=YOUR_MQTT_PASSWORD
```

Merge these lines into the existing local `.env`; do not overwrite unrelated
model, database, or application settings. Keep `.env` out of Git. The default
`config/iot_label_map.json` remains empty; only the separate ESP8266 profile is used
when `ISL_IOT_LABEL_MAP` is set.

Both the backend and ESP8266 connect to the broker using the laptop's current
Wi-Fi IPv4 address, not `127.0.0.1`. The current verified hotspot address is
`10.140.96.1`; set backend `MQTT_HOST` and the four
`ISL_MQTT_BROKER_IP_*` values in `secrets.h` to the current address from
`ipconfig` whenever it changes. The FastAPI web server itself remains bound to
loopback (`127.0.0.1`).

Mosquitto must listen on the laptop's hotspot/LAN interface, allow the configured
user to subscribe to `isl/command/v1` and publish to `isl/ack/v1`, and be allowed
through Windows Firewall only on the intended private network. Do not expose port
1883 to the public internet. MQTT/TCP port 1883 is not TLS encrypted; use only on
the trusted local hotspot for this demo.

## 4. NodeMCU wiring

Use the already-tested wiring:

| Module | NodeMCU | Notes |
|---|---|---|
| OLED SDA | D2 / GPIO4 | I2C data |
| OLED SCL | D1 / GPIO5 | I2C clock |
| OLED VCC | 3V3 | Do not reverse power |
| OLED GND | GND | Common ground |
| Green LED anode | D5 / GPIO14 through its own 220 Ω resistor | LED cathode to GND |
| Yellow LED anode | D6 / GPIO12 through its own 220 Ω resistor | LED cathode to GND |
| 2-pin buzzer positive (+) | D7 / GPIO13 | Direct active-HIGH connection selected for this local test |
| 2-pin buzzer negative (-) | GND | Common ground |

OLED address is `0x3C`. The firmware uses the SSD1306 128x64 driver. Do not connect
LEDs directly to GPIO without their 220-ohm current-limiting resistors. Do not move
outputs to boot-strap pins D3, D4, or D8.

The two-pin buzzer is treated by the selected firmware as active-HIGH: D7 HIGH
activates it and D7 LOW turns it off. During the 60-second `quiet` action it
repeats 10 seconds ON followed by 10 seconds OFF. This gives three 10-second
ON periods separated by 10-second silent periods. At the 60-second deadline,
the firmware turns the buzzer and LEDs OFF and returns the OLED to READY. During the
60-second `i` action, both LEDs stay ON while the buzzer alternates 1 second ON / 1
second OFF. The welcome OLED is text-only and shows exactly `Welcome`, `Dr. Arpan Shah`,
and `Sir`, with no project header.

**Electrical limitation:** The exact buzzer model, rated voltage and operating current
have not been confirmed from the visible HXD marking. Espressif's ESP8266EX datasheet
lists a 12 mA maximum I/O current. Direct GPIO drive is therefore an experimental
user-selected arrangement, not a verified safe electrical design. Do not assume
that the buzzer is within the GPIO current limit. Stop the test immediately if the
NodeMCU resets, heats, emits an unusual smell, or the buzzer behaves abnormally.
A transistor driver is the recommended correction if the buzzer current exceeds the
GPIO capability or remains unknown.

## 5. Arduino IDE setup and upload

Select:

- Board package: ESP8266 by ESP8266 Community, 3.1.2 (same as the previously tested setup).
- Board: NodeMCU 1.0 (ESP-12E Module).
- Port: select the COM port actually shown by Windows (previously COM6).

Install these libraries from Arduino Library Manager:

- PubSubClient by Nick O'Leary.
- ArduinoJson 6.x (6.21.x recommended; do not install ArduinoJson 7 for this sketch).
- Adafruit GFX Library.
- Adafruit SSD1306.

Copy `firmware/esp8266/secrets.example.h` to
`firmware/esp8266/secrets.h`. Edit the Wi-Fi SSID/password, MQTT username/password,
and broker IPv4 in `secrets.h`. The real secrets file is ignored by Git. Do not
remove that ignore rule or commit credentials. Open
`firmware/esp8266/isl_esp8266_node_mcu.ino`, compile and upload.

Open Serial Monitor at 115200 baud. The OLED should show boot, NTP synchronization
and MQTT ready messages. Before running a mapped gesture, test the directly connected
buzzer with one brief, observed `quiet` action. Verify that the NodeMCU remains stable
and that the buzzer repeats 10 seconds ON / 10 seconds OFF during the 60-second action, then turns OFF. The firmware starts SNTP only after Wi-Fi is connected and requires an actual
SNTP time-update callback for the current Wi-Fi connection before connecting to
MQTT. A merely plausible Unix epoch is not treated as proof of synchronization.
Command issue/expiry fields are integer Unix UTC epoch seconds.

## 6. Apply and run on Windows

Create a source backup first. From the synchronized local project folder, apply the
downloaded patch (or check out the feature branch):

```powershell
git status --short
git fetch origin
git switch feature/esp8266-isl-iot-demo
```

If you are applying a standalone patch instead of checking out the branch:

```powershell
git apply --check .\ISL_ESP8266_IOT.patch
git apply .\ISL_ESP8266_IOT.patch
```

Merge the environment values from section 3 into the existing `.env`, restart
the backend, and confirm the health response. Use the existing virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
python -m uvicorn isl_recognition.webapp:app --host 127.0.0.1 --port 8000
```

Open the local app, confirm the model and MediaPipe are ready, then explicitly
enable the IoT toggle. Do not proceed if the health API shows a mapping error,
a mapping count other than three, or the lockdown is still active.

## 7. Protocol and acknowledgement behaviour

The firmware subscribes to `isl/command/v1` at QoS 1 and publishes acknowledgements
to `isl/ack/v1` with retain disabled. PubSubClient only supports QoS 0 publishing,
so device acknowledgements are QoS 0 even though the backend-to-device command path
is QoS 1. The acknowledgement is still an application-level JSON response correlated
by `command_id`; a lost QoS 0 acknowledgement can cause a UI timeout after a physical
action, so verify the hardware output as well as the UI status. The JSON command schema remains version `1.0`
and retains the backend's existing fields:

`schema_version`, `command_id`, `command`, `source_label`, `confidence`,
`capture_id`, `issued_at`, `expires_at`.

The device verifies UUID-shaped IDs, exact label/command pairs, confidence >= 0.30,
integer Unix UTC epoch-second timestamps (to keep expiry arithmetic exact across
embedded JSON implementations), a valid UTC time window and a maximum 30-second TTL. The backend independently requires the selected
expected sign to exactly match the prediction before publishing. The backend creates
10-second commands. The firmware remembers the last 24 acknowledged command/capture pairs
in RAM to suppress QoS redelivery during the current boot. The backend's persisted
one-command-per-capture guard provides the server-side duplicate barrier.

Acknowledgements use the backend-supported statuses `acknowledged`, `rejected`,
and `duplicate`. A broker PUBACK only confirms broker receipt; it is not proof of
device action. The UI's IoT history must show a device acknowledgement to establish
that the subscriber processed the command.

## 8. Test sequence and acceptance

1. With the lock at its default, call `/api/health`: `pre_iot_lockdown` must be true
   and the UI toggle must be disabled.
2. Opt in locally, restart the server, verify the health response, then enable the
   UI toggle.
3. Select `quiet` as expected sign, perform it with confidence at or above 0.30,
   and confirm green LED, OLED `QUIET!`, the repeating 10 s ON / 10 s OFF buzzer pattern and a correlated acknowledgement.
4. Select `time` as expected sign, perform it and confirm yellow LED, a live
   HH:MM:SS clock in Asia/Kolkata time, no buzzer and a correlated acknowledgement.
5. Select `i` as expected sign, perform it and confirm both LEDs remain ON, the OLED
   shows exactly the three welcome lines, and the buzzer alternates 1 s ON / 1 s OFF
   until all outputs stop at 60 s.
6. Select `hello` as expected sign, perform it and confirm browser text only; hello
   has no approved IoT mapping, so no device command is sent.
7. Deliberately select `hello` but perform `quiet` (and vice versa). Confirm
   `expected_sign_mismatch` and no hardware action.
8. Test confidence below 0.30, unavailable broker and duplicate delivery.
   They must not cause an additional physical action.
9. Turn IoT OFF in the UI when testing is complete. Remove
   `ISL_IOT_HARDWARE_DEMO` and `ISL_IOT_LABEL_MAP` from `.env`, or set the demo
   flag to false, then restart the backend to restore the default safety lock.

Automated protocol tests cover mapping, valid commands, confidence rejection,
expiry validation and simulator duplicate handling. They do not prove Arduino
compilation, Wi-Fi/MQTT interoperability, OLED rendering, physical GPIO behaviour
or electrical safety. Record those separately after testing the real NodeMCU.

## 9. Known boundaries

- Each visible feedback action lasts 60 seconds: QUIET keeps the green LED and `QUIET!` on the OLED; TIME keeps the yellow LED and live IST clock on the OLED. The QUIET buzzer repeats 10 seconds ON / 10 seconds OFF during the 60-second action, then turns OFF. For `i`, both LEDs remain ON and the buzzer alternates 1 second ON / 1 second OFF for the 60-second welcome action. HELLO is text-only. The device rejects additional actions while one is active.
- The device requires working Wi-Fi and NTP before accepting commands.
- Duplicate IDs are held in RAM for the current boot; device reboot clears that
  local cache. Server-side one-command-per-capture persistence remains in effect.
- Port 1883 is unencrypted. This is a trusted local-network demonstration only.
- This feature branch does not claim production readiness or general-purpose ISL
  translation. Existing research, dataset provenance and recognition release gates
  remain separate.
