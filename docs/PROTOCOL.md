# Protocol Contracts (WebSocket v1.1 record-then-extract and MQTT v1.0)

## WebSocket endpoint

`ws://127.0.0.1:8000/ws/capture`. The browser sends UTF-8 JSON text; WebSocket protocol v1.1 does not use binary WebSocket messages. Uvicorn is launched with a 4,000,000-byte WebSocket message cap, and the application separately enforces JPEG byte and JSON-message bounds. Frame timestamps are Unix seconds and must be recent (not more than 120 seconds old or 5 seconds in the future) and strictly monotonic within a capture; a repeated/backward timestamp is rejected.

### Client messages

- `{"type":"start"}` — begins one capture; server returns `capture_started` or an explicit readiness/lock error.
- `{"type":"frame","timestamp":<unix_seconds>,"jpeg_b64":"<base64 JPEG>"}` — submits one JPEG. Optional prefix `data:image/jpeg;base64,` is accepted.
- `{"type":"stop","client_frames_dropped":0}` — submit; stops accepting new frames, then sequentially processes the JPEG frames already buffered for this capture and returns one result.
- `{"type":"cancel"}` — discard the unfinished capture; no inference or command is performed.
- `{"type":"ping"}` — returns `pong`.

### Server message types

`connected`, `capture_started`, `progress`, `time_limit_reached`, `frame_limit_reached`, `processing_started`, `processing_progress`, `frame_error`, `result`, `cancelled`, `pong`, or `error`. Error messages use stable `code` values for client handling. Recording progress reports accepted buffered frames (max 169) and duration. The legacy `queue_dropped` progress field remains zero for UI compatibility; the current primary capture backend does not have a server-side frame queue. The browser may skip a send when WebSocket buffered data is high, and sends its client-side drop count with `stop`. Processing progress reports sequential frames processed, successful feature extractions and failures. The result contains label/confidence when available, outcome/status, frame/drop telemetry, duration/latency, pose/hand ratios and rejection/error reason. No raw frames are persisted.

### Lifecycle and quality checks

Only one capture/inference may be active in the process. Defaults in `app_config.py` are 12 extracted frames minimum, pose coverage >=50%, hand coverage >=30%, and extraction failure ratio <=30%; a local `.env` can override some values, so inspect the active configuration before a run. These are provisional engineering gates, not data-optimised thresholds. The web path accepts up to 169 captured JPEG frames, then extracts them after Stop; feature sequences are preprocessed to the established `(169,134)` input. The frame cap is a capture limit, not a continuous queue or live inference mechanism.

## MQTT command protocol v1.0

### Topics

- Command: `isl/command/v1` (configurable through `MQTT_COMMAND_TOPIC`)
- Ack: `isl/ack/v1` (configurable through `MQTT_ACK_TOPIC`)

The backend reserves each capture ID in a private SQLite idempotency ledger before publish. The ledger is separate from visible event history and survives history reset/retention; an existing database is backfilled from prior IoT rows. A unique index on `iot_events.capture_id` provides a second guard. Recognition-event upserts are in-place so they cannot cascade-delete the IoT record. The simulator deduplicates capture IDs in its process lifetime. The ESP32 also stores command/capture IDs in a bounded 64-entry Preferences/NVS ring and checks that its pointer is valid. The backend/device behavior is at-most-once protection, not exactly-once delivery: once the backend reserves a capture, it does not automatically retry that capture if publish or hardware execution fails. The user should make a new capture after diagnosing the failure. The publisher records its timestamp immediately before calling Paho publish so system benchmarks can estimate publish-attempt-to-subscriber-ACK latency. The publisher sends only for a successfully inferred, accepted, allowlisted prediction, confidence at least the separate IoT minimum, explicit session IoT enablement, exactly-two-label configuration, and cooldown success. The shipped mapping is empty. The two source labels and command pairs must be configured independently in the backend JSON and firmware build. No arbitrary sign string is an actuator command.

### Command JSON

```json
{
  "schema_version": "1.0",
  "command_id": "<UUID>",
  "command": "LED_BLINK",
  "source_label": "<one of the two configured labels>",
  "confidence": 0.91,
  "capture_id": "<capture UUID>",
  "issued_at": 1793000000.0,
  "expires_at": 1793000010.0
}
```

The example values are explanatory only; the application generates IDs and timestamps for real commands. Receiver validation checks schema, UUID, command/label allowlist, finite numeric values, confidence, issuance skew and expiry (maximum 30-second validity). The current publisher sets a 10-second TTL.

### Acknowledgement JSON

```json
{
  "schema_version": "1.0",
  "command_id": "<same UUID>",
  "status": "acknowledged",
  "detail": "simulated_led_blink_processed",
  "timestamp_utc": "<UTC ISO-8601 timestamp>"
}
```

Allowed statuses: `acknowledged`, `rejected`, `duplicate`. Acknowledgement means receiver-side validation/processing, not proof of real LED output. UI/history also distinguish `pending`, `timed_out` and `publish_failed`. Uncertain physical action is not automatically republished. Broker publish success alone does not equal subscriber/device success.

## Safety and network assumptions

- IoT is hard-locked in the consolidated pre-IoT release; UI/API enable attempts are denied and no device command is published. The default `config/iot_label_map.json` is intentionally empty. A later reviewed IoT release requires exactly two owner-selected labels, matching firmware compile-time allowlist pairs, full software validation, authenticated/restricted broker configuration and electrical review.
- Server-side checks are mandatory; browser controls alone are not trusted.
- Simulator validates independently and deduplicates IDs. Firmware mirrors contract validation and stores a bounded set of processed IDs in NVS. The backend ledger is permanent across UI history deletion; the firmware ring retains the most recent 64 IDs, not an unlimited history.
- The supplied Mosquitto sample binds to loopback and permits anonymous access for local demo only. Do not use that sample as a LAN configuration for a physical ESP32. For future LAN use, configure authentication, topic ACLs and firewall rules.
