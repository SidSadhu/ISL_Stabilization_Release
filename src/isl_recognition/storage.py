from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventStore:
    """Small SQLite repository. Every operation uses its own connection for safe thread use."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS recognition_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                predicted_label TEXT,
                confidence REAL,
                processing_latency_ms REAL,
                capture_duration_ms REAL,
                frame_count INTEGER NOT NULL,
                frames_dropped INTEGER NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL,
                reason TEXT,
                device TEXT,
                mediapipe_latency_ms REAL,
                inference_latency_ms REAL,
                effective_fps REAL,
                end_to_end_latency_ms REAL,
                received_frames INTEGER NOT NULL DEFAULT 0,
                extraction_failures INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_recognition_created ON recognition_events(created_at DESC);
            CREATE TABLE IF NOT EXISTS iot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL UNIQUE,
                capture_id TEXT NOT NULL,
                mapped_command TEXT NOT NULL,
                source_label TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL,
                ack_deadline REAL NOT NULL,
                publish_status TEXT NOT NULL DEFAULT 'not_attempted',
                publish_attempt_at TEXT,
                ack_status TEXT NOT NULL DEFAULT 'pending',
                ack_at TEXT,
                ack_detail TEXT,
                command_json TEXT NOT NULL,
                FOREIGN KEY(capture_id) REFERENCES recognition_events(capture_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_iot_created ON iot_events(created_at DESC);
            """)
            # Capture-level uniqueness is a safety invariant: one recognition capture may
            # produce at most one hardware command, even if a caller retries. Refuse to
            # silently discard historical rows if an older DB somehow contains duplicates.
            duplicate_capture = conn.execute(
                "SELECT capture_id, COUNT(*) AS n FROM iot_events GROUP BY capture_id HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            if duplicate_capture is not None:
                raise RuntimeError(
                    "Existing IoT history contains multiple commands for capture "
                    f"{duplicate_capture['capture_id']!r}; back up and audit this database before starting."
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_iot_capture_unique ON iot_events(capture_id)"
            )
            # Keep the idempotency ledger separate from visible event history. A user
            # may reset/prune history, but doing so must not make a previously actuated
            # capture eligible for another command.
            conn.execute("""CREATE TABLE IF NOT EXISTS iot_capture_dedup (
                capture_id TEXT PRIMARY KEY NOT NULL,
                command_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""INSERT OR IGNORE INTO iot_capture_dedup (capture_id, command_id, created_at)
                SELECT capture_id, command_id, created_at FROM iot_events""")
            # Small schema migration for existing local databases created by prior app versions.
            current = {row[1] for row in conn.execute("PRAGMA table_info(recognition_events)").fetchall()}
            additions = {
                "mediapipe_latency_ms": "REAL",
                "inference_latency_ms": "REAL",
                "effective_fps": "REAL",
                "end_to_end_latency_ms": "REAL",
                "received_frames": "INTEGER NOT NULL DEFAULT 0",
                "extraction_failures": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, sql_type in additions.items():
                if column not in current:
                    conn.execute(f"ALTER TABLE recognition_events ADD COLUMN {column} {sql_type}")
            iot_current = {row[1] for row in conn.execute("PRAGMA table_info(iot_events)").fetchall()}
            if "publish_attempt_at" not in iot_current:
                conn.execute("ALTER TABLE iot_events ADD COLUMN publish_attempt_at TEXT")

    def add_recognition(self, event: dict[str, Any]) -> None:
        with self._connect() as conn:
            # Do not use INSERT OR REPLACE here: SQLite implements REPLACE as
            # DELETE + INSERT. The recognition row is the parent of iot_events,
            # so a duplicate capture write could cascade-delete the durable IoT
            # deduplication record and make the same capture eligible again.
            conn.execute("""INSERT INTO recognition_events
                (capture_id,created_at,predicted_label,confidence,processing_latency_ms,capture_duration_ms,
                 frame_count,frames_dropped,outcome,reason,device,mediapipe_latency_ms,inference_latency_ms,effective_fps,end_to_end_latency_ms,received_frames,extraction_failures)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(capture_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    predicted_label=excluded.predicted_label,
                    confidence=excluded.confidence,
                    processing_latency_ms=excluded.processing_latency_ms,
                    capture_duration_ms=excluded.capture_duration_ms,
                    frame_count=excluded.frame_count,
                    frames_dropped=excluded.frames_dropped,
                    outcome=excluded.outcome,
                    reason=excluded.reason,
                    device=excluded.device,
                    mediapipe_latency_ms=excluded.mediapipe_latency_ms,
                    inference_latency_ms=excluded.inference_latency_ms,
                    effective_fps=excluded.effective_fps,
                    end_to_end_latency_ms=excluded.end_to_end_latency_ms,
                    received_frames=excluded.received_frames,
                    extraction_failures=excluded.extraction_failures""", (
                    event["capture_id"], event.get("created_at", utc_now()), event.get("predicted_label"),
                    event.get("confidence"), event.get("processing_latency_ms"), event.get("capture_duration_ms"),
                    int(event.get("frame_count", 0)), int(event.get("frames_dropped", 0)), event["outcome"],
                    event.get("reason"), event.get("device"), event.get("mediapipe_latency_ms"),
                    event.get("inference_latency_ms"), event.get("effective_fps"), event.get("end_to_end_latency_ms"),
                    int(event.get("received_frames", 0)), int(event.get("extraction_failures", 0)),
                ))

    def add_iot_event(self, event: dict[str, Any]) -> bool:
        """Persist one command per capture, independently of event-history retention.

        The private idempotency ledger is inserted in the same SQLite transaction as
        the visible event. A duplicate capture is rejected even after event history has
        been reset or pruned. SQLite uniqueness makes this atomic across threads and
        process restarts.
        """
        with self._connect() as conn:
            ledger = conn.execute("""INSERT INTO iot_capture_dedup
                (capture_id, command_id, created_at) VALUES (?,?,?)
                ON CONFLICT(capture_id) DO NOTHING""", (
                event["capture_id"], event["command_id"], event.get("created_at", utc_now()),
            ))
            if ledger.rowcount != 1:
                return False
            conn.execute("""INSERT INTO iot_events
                (command_id,capture_id,mapped_command,source_label,confidence,created_at,ack_deadline,
                 publish_status,ack_status,command_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                event["command_id"], event["capture_id"], event["mapped_command"], event["source_label"],
                float(event["confidence"]), event.get("created_at", utc_now()), float(event["ack_deadline"]),
                event.get("publish_status", "not_attempted"), event.get("ack_status", "pending"),
                json.dumps(event["command_json"], separators=(",", ":")),
            ))
            return True

    def has_iot_event_for_capture(self, capture_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM iot_capture_dedup WHERE capture_id=? LIMIT 1", (capture_id,)
            ).fetchone()
            return row is not None

    def mark_publish_attempt(self, command_id: str, timestamp: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE iot_events SET publish_attempt_at=? WHERE command_id=?", (timestamp or utc_now(), command_id))

    def update_publish(self, command_id: str, success: bool, detail: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE iot_events SET publish_status=?, ack_status=CASE WHEN ? THEN CASE WHEN ack_status IN ('acknowledged','rejected','duplicate') THEN ack_status ELSE 'pending' END ELSE 'publish_failed' END, ack_detail=CASE WHEN ack_status IN ('acknowledged','rejected','duplicate') AND ? THEN ack_detail ELSE ? END WHERE command_id=?", (
                "published" if success else "failed", 1 if success else 0, 1 if success else 0, detail, command_id
            ))

    def update_ack(self, command_id: str, status: str, detail: str | None = None) -> bool:
        if status not in {"acknowledged", "rejected", "duplicate"}:
            return False
        with self._connect() as conn:
            cur = conn.execute("UPDATE iot_events SET ack_status=?, ack_at=?, ack_detail=? WHERE command_id=? AND ack_status='pending' AND publish_status IN ('publishing','published')", (status, utc_now(), detail, command_id))
            return cur.rowcount > 0

    def mark_timeouts(self, now_epoch: float) -> int:
        with self._connect() as conn:
            cur = conn.execute("UPDATE iot_events SET ack_status='timed_out', ack_at=?, ack_detail='No acknowledgement received before timeout' WHERE ack_status='pending' AND publish_status='published' AND ack_deadline < ?", (utc_now(), now_epoch))
            return cur.rowcount

    def recognition_history(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM recognition_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def iot_history(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute("SELECT command_id,capture_id,mapped_command,source_label,confidence,created_at,publish_status,publish_attempt_at,ack_status,ack_at,ack_detail FROM iot_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def reset_history(self) -> dict[str, int]:
        # This deletes visible history only. The private iot_capture_dedup ledger is
        # intentionally retained so clearing the UI cannot enable command replay.
        with self._connect() as conn:
            iot = conn.execute("SELECT COUNT(*) FROM iot_events").fetchone()[0]
            rec = conn.execute("SELECT COUNT(*) FROM recognition_events").fetchone()[0]
            conn.execute("DELETE FROM iot_events")
            conn.execute("DELETE FROM recognition_events")
        return {"recognition_deleted": rec, "iot_deleted": iot}

    def delete_before(self, timestamp_iso: str) -> dict[str, int]:
        # Retention prunes visible events, never the permanent capture idempotency ledger.
        with self._connect() as conn:
            iot = conn.execute("DELETE FROM iot_events WHERE created_at < ?", (timestamp_iso,)).rowcount
            rec = conn.execute("DELETE FROM recognition_events WHERE created_at < ?", (timestamp_iso,)).rowcount
        return {"recognition_deleted": rec, "iot_deleted": iot}
