from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isl_recognition.storage import EventStore, utc_now


def test_sqlite_recognition_and_iot_history_persist_and_are_separate(tmp_path):
    db = tmp_path / "events.sqlite3"
    store = EventStore(db)
    store.add_recognition({"capture_id":"capture-1","outcome":"accepted","predicted_label":"hello","confidence":.91,"frame_count":20})
    command = {"schema_version":"1.0","command_id":"00000000-0000-4000-8000-000000000001","command":"LED_BLINK"}
    store.add_iot_event({"command_id":command["command_id"],"capture_id":"capture-1","mapped_command":"LED_BLINK","source_label":"hello","confidence":.91,"created_at":utc_now(),"ack_deadline":9999999999,"publish_status":"published","ack_status":"pending","command_json":command})
    store.update_ack(command["command_id"], "acknowledged", "simulator response")
    reopened = EventStore(db)
    assert reopened.recognition_history()[0]["predicted_label"] == "hello"
    event = reopened.iot_history()[0]
    assert event["ack_status"] == "acknowledged"
    assert len(reopened.recognition_history()) == 1 and len(reopened.iot_history()) == 1


def test_timeout_reset_and_history_view_do_not_republish(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.add_recognition({"capture_id":"capture-2","outcome":"accepted","frame_count":12})
    command = {"schema_version":"1.0","command_id":"00000000-0000-4000-8000-000000000002"}
    store.add_iot_event({"command_id":command["command_id"],"capture_id":"capture-2","mapped_command":"LED_BLINK","source_label":"hello","confidence":.9,"created_at":utc_now(),"ack_deadline":1,"publish_status":"published","ack_status":"pending","command_json":command})
    assert store.mark_timeouts(now_epoch=2) == 1
    assert store.iot_history()[0]["ack_status"] == "timed_out"
    assert store.reset_history() == {"recognition_deleted":1,"iot_deleted":1}
    assert store.recognition_history() == [] and store.iot_history() == []
    # Visible history reset must not erase the private anti-replay ledger.
    assert store.has_iot_event_for_capture("capture-2") is True
    retry = {"command_id": "00000000-0000-4000-8000-000000000099", "capture_id": "capture-2",
             "mapped_command": "LED_BLINK", "source_label": "hello", "confidence": .9,
             "ack_deadline": 9999999999, "publish_status": "publishing", "ack_status": "pending",
             "command_json": {"schema_version": "1.0"}}
    assert store.add_iot_event(retry) is False


def test_publish_attempt_timestamp_is_persisted_and_used_before_ack(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.add_recognition({"capture_id":"capture-3","outcome":"accepted","frame_count":12})
    command = {"schema_version":"1.0","command_id":"00000000-0000-4000-8000-000000000003"}
    store.add_iot_event({"command_id":command["command_id"],"capture_id":"capture-3","mapped_command":"LED_BLINK","source_label":"hello","confidence":.9,"created_at":utc_now(),"ack_deadline":9999999999,"publish_status":"publishing","ack_status":"pending","command_json":command})
    store.mark_publish_attempt(command["command_id"], "2026-10-26T10:00:00.000Z")
    assert store.update_ack(command["command_id"], "acknowledged", "fast subscriber")
    store.update_publish(command["command_id"], True, "broker publish confirmed")
    event=store.iot_history()[0]
    assert event["publish_attempt_at"] == "2026-10-26T10:00:00.000Z"
    assert event["ack_status"] == "acknowledged" and event["publish_status"] == "published"


def test_iot_storage_allows_only_one_command_per_capture_across_retries(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.add_recognition({"capture_id":"capture-once","outcome":"accepted","frame_count":20})

    def event(command_id):
        command = {"schema_version":"1.0","command_id":command_id,"command":"LED_BLINK"}
        return {
            "command_id":command_id, "capture_id":"capture-once", "mapped_command":"LED_BLINK",
            "source_label":"hello", "confidence":.95, "ack_deadline":9999999999,
            "publish_status":"publishing", "ack_status":"pending", "command_json":command,
        }

    assert store.add_iot_event(event("00000000-0000-4000-8000-000000000101")) is True
    assert store.add_iot_event(event("00000000-0000-4000-8000-000000000102")) is False
    assert store.has_iot_event_for_capture("capture-once") is True
    assert len(store.iot_history()) == 1


def test_existing_database_with_duplicate_capture_commands_fails_closed(tmp_path):
    import json
    import sqlite3
    import pytest

    db = tmp_path / "legacy-events.sqlite3"
    store = EventStore(db)
    store.add_recognition({"capture_id":"legacy-capture","outcome":"accepted","frame_count":20})

    def payload(command_id):
        return {
            "command_id": command_id,
            "capture_id": "legacy-capture",
            "mapped_command": "LED_BLINK",
            "source_label": "hello",
            "confidence": .95,
            "ack_deadline": 9999999999,
            "publish_status": "published",
            "ack_status": "pending",
            "command_json": {"schema_version":"1.0","command_id":command_id},
        }

    assert store.add_iot_event(payload("00000000-0000-4000-8000-000000000201")) is True
    with sqlite3.connect(db) as conn:
        conn.execute("DROP INDEX idx_iot_capture_unique")
        second = payload("00000000-0000-4000-8000-000000000202")
        conn.execute(
            """INSERT INTO iot_events
               (command_id,capture_id,mapped_command,source_label,confidence,created_at,ack_deadline,
                publish_status,ack_status,command_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (second["command_id"], second["capture_id"], second["mapped_command"], second["source_label"],
             second["confidence"], utc_now(), second["ack_deadline"], second["publish_status"],
             second["ack_status"], json.dumps(second["command_json"])),
        )

    with pytest.raises(RuntimeError, match="multiple commands for capture"):
        EventStore(db)


def test_duplicate_recognition_upsert_does_not_delete_iot_capture_dedup_record(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.add_recognition({"capture_id": "same-capture", "outcome": "accepted", "predicted_label": "hello", "confidence": .95, "frame_count": 20})
    command = {"schema_version": "1.0", "command_id": "00000000-0000-4000-8000-000000000301", "command": "LED_BLINK"}
    assert store.add_iot_event({
        "command_id": command["command_id"], "capture_id": "same-capture", "mapped_command": "LED_BLINK",
        "source_label": "hello", "confidence": .95, "ack_deadline": 9999999999,
        "publish_status": "published", "ack_status": "pending", "command_json": command,
    }) is True

    # Replaying/upserting the same recognition capture must not cascade-delete its
    # IoT command row, otherwise a new command_id could actuate a second time.
    store.add_recognition({"capture_id": "same-capture", "outcome": "accepted", "predicted_label": "hello", "confidence": .96, "frame_count": 21})
    assert store.has_iot_event_for_capture("same-capture") is True
    duplicate_id = "00000000-0000-4000-8000-000000000302"
    duplicate = {
        "command_id": duplicate_id, "capture_id": "same-capture", "mapped_command": "LED_BLINK",
        "source_label": "hello", "confidence": .96, "ack_deadline": 9999999999,
        "publish_status": "publishing", "ack_status": "pending",
        "command_json": {"schema_version": "1.0", "command_id": duplicate_id},
    }
    assert store.add_iot_event(duplicate) is False
    assert len(store.iot_history()) == 1


def test_retention_pruning_does_not_clear_iot_capture_idempotency_ledger(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.add_recognition({"capture_id": "retained-capture", "outcome": "accepted", "frame_count": 20})
    command_id = "00000000-0000-4000-8000-000000000401"
    command = {"schema_version": "1.0", "command_id": command_id}
    assert store.add_iot_event({
        "command_id": command_id, "capture_id": "retained-capture", "mapped_command": "LED_BLINK",
        "source_label": "hello", "confidence": .95, "created_at": "2026-01-01T00:00:00.000Z",
        "ack_deadline": 9999999999, "publish_status": "published", "ack_status": "pending", "command_json": command,
    }) is True
    store.delete_before("2026-02-01T00:00:00.000Z")
    assert store.iot_history() == []
    assert store.has_iot_event_for_capture("retained-capture") is True
    retry_id = "00000000-0000-4000-8000-000000000402"
    assert store.add_iot_event({
        "command_id": retry_id, "capture_id": "retained-capture", "mapped_command": "LED_BLINK",
        "source_label": "hello", "confidence": .95, "ack_deadline": 9999999999,
        "publish_status": "publishing", "ack_status": "pending", "command_json": {"schema_version": "1.0"},
    }) is False


def test_existing_iot_events_backfill_private_idempotency_ledger(tmp_path):
    db = tmp_path / "events.sqlite3"
    store = EventStore(db)
    store.add_recognition({"capture_id": "legacy-backed-up-capture", "outcome": "accepted", "frame_count": 20})
    command_id = "00000000-0000-4000-8000-000000000501"
    assert store.add_iot_event({
        "command_id": command_id, "capture_id": "legacy-backed-up-capture", "mapped_command": "LED_BLINK",
        "source_label": "hello", "confidence": .95, "ack_deadline": 9999999999,
        "publish_status": "published", "ack_status": "pending",
        "command_json": {"schema_version": "1.0", "command_id": command_id},
    }) is True

    # Simulate a prior app database without the new private ledger; reopening
    # migrates existing visible IoT rows before the user can reset/prune history.
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE iot_capture_dedup")
    migrated = EventStore(db)
    assert migrated.has_iot_event_for_capture("legacy-backed-up-capture") is True
    retry_id = "00000000-0000-4000-8000-000000000502"
    assert migrated.add_iot_event({
        "command_id": retry_id, "capture_id": "legacy-backed-up-capture", "mapped_command": "LED_BLINK",
        "source_label": "hello", "confidence": .95, "ack_deadline": 9999999999,
        "publish_status": "publishing", "ack_status": "pending",
        "command_json": {"schema_version": "1.0", "command_id": retry_id},
    }) is False
