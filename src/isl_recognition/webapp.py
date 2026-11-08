from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import json
import math
import time
import uuid
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .app_config import PROJECT_ROOT, Settings, resolve_label_map
from .feature_extractor import MediaPipeFeatureExtractor
from .iot_protocol import build_command, load_label_commands
from .labels import invert_label_map, load_label_map
from .mqtt_gateway import MQTTGateway
from .preprocess import preprocess_feature_sequence
from .storage import EventStore, utc_now


@dataclass
class RuntimeServices:
    settings: Settings
    store: EventStore
    model: Any | None = None
    labels: dict[str, int] = field(default_factory=dict)
    extractor: Any | None = None
    mqtt: Any | None = None
    model_error: str | None = None
    label_error: str | None = None
    extractor_error: str | None = None
    iot_label_commands: dict[str, str] = field(default_factory=dict)
    iot_mapping_error: str | None = None
    iot_enabled: bool = False
    last_iot_command_epoch: float = 0.0

    @property
    def model_ready(self) -> bool:
        return self.model is not None and len(self.labels) == 50

    @property
    def extractor_ready(self) -> bool:
        if self.extractor is None:
            return False
        return bool(getattr(self.extractor, "available", True))


def build_services(settings: Settings | None = None) -> RuntimeServices:
    settings = settings or Settings()
    settings.validate()
    store = EventStore(settings.database_path)
    services = RuntimeServices(settings=settings, store=store, extractor=MediaPipeFeatureExtractor())

    label_path = resolve_label_map(settings)
    if not label_path or not label_path.is_file():
        services.label_error = (
            "Official INCLUDE-50 label map not found. Set ISL_LABEL_MAP to the JSON label map "
            "inside your verified AI4Bharat/INCLUDE checkout. The application will not invent class ordering."
        )
    else:
        try:
            services.labels = load_label_map(label_path)
        except Exception as exc:
            services.label_error = f"Label map validation failed: {type(exc).__name__}: {exc}"

    # The IoT allowlist is independent from classifier labels. Missing, malformed,
    # or incomplete configuration fails closed and never disables text recognition.
    try:
        if not services.labels:
            raise ValueError("Official INCLUDE-50 label map must load before validating IoT mappings.")
        services.iot_label_commands = load_label_commands(settings.iot_label_map_path, services.labels)
    except Exception as exc:
        services.iot_label_commands = {}
        services.iot_mapping_error = f"IoT mapping not loaded: {type(exc).__name__}: {exc}"

    if settings.ai4bharat_repo is None or settings.checkpoint is None:
        services.model_error = (
            "Model assets are not configured. Set AI4BHARAT_REPO and ISL_CHECKPOINT to the official "
            "AI4Bharat/INCLUDE source checkout and verified include50_no_cnn_transformer_small.pth checkpoint."
        )
    elif not settings.ai4bharat_repo.is_dir():
        services.model_error = f"AI4Bharat repository path does not exist: {settings.ai4bharat_repo}"
    elif not settings.checkpoint.is_file():
        services.model_error = f"Configured checkpoint does not exist: {settings.checkpoint}"
    elif services.label_error:
        services.model_error = "Model loading blocked because official label-map validation failed."
    else:
        try:
            # Import Torch/model code only after external assets are configured, so the UI and
            # health API can still start in a core-only environment and report the real blocker.
            from .model import INCLUDETransformerSmall
            services.model = INCLUDETransformerSmall(settings.ai4bharat_repo, settings.checkpoint)
            services.model_error = None
        except Exception as exc:
            services.model_error = f"Official model initialisation failed: {type(exc).__name__}: {exc}"
            services.model = None

    if not services.extractor_ready:
        services.extractor_error = "MediaPipe/OpenCV are not installed in this Python environment. Install requirements-mediapipe.txt."
    return services



_DEBUG_CAPTURE_MAX_BYTES = 32 * 1024 * 1024


def _safe_camera_settings(value: Any) -> dict[str, Any] | None:
    """Keep only non-identifying camera telemetry supplied by the local browser."""
    if not isinstance(value, dict):
        return None
    output: dict[str, Any] = {}
    for key in ("width", "height", "frameRate", "aspectRatio", "videoElementWidth",
                "videoElementHeight", "requestedWidth", "requestedHeight", "requestedFrameRate"):
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)):
            output[key] = float(item) if key in ("frameRate", "aspectRatio", "requestedFrameRate") else int(item)
    for key in ("facingMode", "requestedAspectRatio"):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 32:
            output[key] = item
    return output or None


def _frame_timing_summary(jpeg_frames: list[dict[str, Any]]) -> dict[str, float | int | None]:
    timestamps = [float(item["timestamp"]) for item in jpeg_frames if isinstance(item.get("timestamp"), (int, float))]
    if len(timestamps) < 2:
        return {
            "client_timestamp_fps": None,
            "frame_interval_mean_ms": None,
            "frame_interval_median_ms": None,
            "frame_interval_p95_ms": None,
            "frame_interval_count": max(0, len(timestamps) - 1),
        }
    intervals = np.diff(np.asarray(timestamps, dtype=np.float64)) * 1000.0
    intervals = intervals[np.isfinite(intervals) & (intervals > 0)]
    if not len(intervals):
        fps = None
        mean = median = p95 = None
    else:
        median = float(np.median(intervals))
        fps = float(1000.0 / median) if median > 0 else None
        mean = float(np.mean(intervals))
        p95 = float(np.percentile(intervals, 95))
    return {
        "client_timestamp_fps": fps,
        "frame_interval_mean_ms": mean,
        "frame_interval_median_ms": median,
        "frame_interval_p95_ms": p95,
        "frame_interval_count": int(len(intervals)),
    }


def _persist_debug_capture(
    capture: dict[str, Any],
    frame_metrics: list[dict[str, Any]],
    features: list[np.ndarray],
    summary: dict[str, Any],
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Persist opt-in local capture evidence. Never called unless the user checks the box."""
    jpeg_frames = capture.get("jpeg_frames", [])
    total_bytes = sum(len(item.get("jpeg", b"")) for item in jpeg_frames)
    if total_bytes > _DEBUG_CAPTURE_MAX_BYTES:
        return {"saved": False, "error": f"Capture is {total_bytes / (1024 * 1024):.1f} MiB; the local debug-save limit is 32 MiB."}
    try:
        capture_id = str(uuid.UUID(str(capture.get("capture_id"))))
    except Exception:
        return {"saved": False, "error": "Capture identifier was invalid; debug files were not written."}

    root = Path(project_root or PROJECT_ROOT) / "outputs" / "debug_captures"
    root.mkdir(parents=True, exist_ok=True)
    target = root / capture_id
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{capture_id}_", dir=str(root)))
    try:
        frames_dir = temp_dir / "frames"
        frames_dir.mkdir()
        manifest_frames = []
        for index, item in enumerate(jpeg_frames, start=1):
            filename = f"frame_{index:04d}.jpg"
            (frames_dir / filename).write_bytes(item["jpeg"])
            frame_record = dict(frame_metrics[index - 1]) if index - 1 < len(frame_metrics) else {"frame_index": index - 1}
            frame_record.update({
                "filename": f"frames/{filename}",
                "client_timestamp_unix_seconds": float(item.get("timestamp", 0.0)),
                "jpeg_bytes": len(item["jpeg"]),
            })
            manifest_frames.append(frame_record)
        if features:
            matrix = np.stack(features, axis=0).astype(np.float32)
            np.savetxt(temp_dir / "raw_features_134d.csv", matrix, delimiter=",", fmt="%.8g")
            # Store the exact interpolated/truncated/padded tensors used by inference.
            # This is a diagnostic copy only; the classifier path itself is unchanged.
            processed_raw, model_input = preprocess_feature_sequence(matrix, max_frame_len=169, feature_dim=134)
            np.savetxt(temp_dir / "interpolated_sequence_134d.csv", processed_raw, delimiter=",", fmt="%.8g")
            np.savetxt(temp_dir / "model_input_169x134.csv", model_input, delimiter=",", fmt="%.8g")
        metadata = {
            "schema_version": 1,
            "capture_id": capture_id,
            "privacy": "Explicitly opted-in local debug capture. Contains the user's webcam imagery. Do not share publicly; delete after debugging.",
            "expected_label": capture.get("expected_label"),
            "camera_settings": capture.get("camera_settings"),
            "frame_timing": capture.get("frame_timing", {}),
            "summary": summary,
            "frame_count_received": len(jpeg_frames),
            "feature_rows_saved": len(features),
            "frames": manifest_frames,
            "files": ["frames/frame_XXXX.jpg (one per received frame)"] + ([
                "raw_features_134d.csv", "interpolated_sequence_134d.csv", "model_input_169x134.csv"
            ] if features else []),
        }
        (temp_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        if target.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"saved": False, "error": "A debug folder with this capture ID already exists."}
        temp_dir.replace(target)
        return {"saved": True, "directory": str(target.relative_to(Path(project_root or PROJECT_ROOT))), "bytes": total_bytes}
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"saved": False, "error": f"{type(exc).__name__}: {exc}"}


def _write_debug_result(result: dict[str, Any]) -> None:
    """Write a small final prediction/result record beside an opt-in debug capture."""
    if not result.get("debug_capture_saved") or not result.get("debug_capture_directory"):
        return
    try:
        root = (Path(PROJECT_ROOT) / "outputs" / "debug_captures").resolve()
        target = (Path(PROJECT_ROOT) / str(result["debug_capture_directory"])).resolve()
        if root not in target.parents:
            return
        fields = (
            "capture_id", "expected_label", "predicted_label", "confidence", "outcome", "reason",
            "frame_count", "received_frames", "frames_dropped", "pose_ratio", "hand_ratio",
            "hand1_ratio", "hand2_ratio", "both_hand_ratio", "client_timestamp_fps",
            "frame_interval_median_ms", "frame_interval_p95_ms", "inference_latency_ms",
        )
        payload = {key: result.get(key) for key in fields}
        if isinstance(result.get("top5_predictions"), list):
            payload["top5_predictions"] = result["top5_predictions"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        # Debug persistence must never turn a completed recognition into a failure.
        return

def create_app(services: RuntimeServices | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.services is None:
            app.state.services = build_services()
        # A process-local default is always OFF, regardless of past history or environment settings.
        app.state.services.iot_enabled = False

        async def timeout_sweeper() -> None:
            while True:
                try:
                    app.state.services.store.mark_timeouts(time.time())
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        app.state.timeout_task = asyncio.create_task(timeout_sweeper())
        try:
            yield
        finally:
            task = app.state.timeout_task
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            current = app.state.services
            if current and current.mqtt:
                current.mqtt.close()

    app = FastAPI(
        title="ISL INCLUDE-50 Recognition MVP",
        version="1.0.0-rc3",
        description="Local-only manual isolated-sign recognition with optional gated MQTT commands.",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.capture_lock = asyncio.Lock()
    app.state.timeout_task = None
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health():
        svc: RuntimeServices = app.state.services
        return {
            "status": "ready" if svc.model_ready and svc.extractor_ready else "degraded",
            "host_policy": "loopback_only",
            "model_ready": svc.model_ready,
            "model_error": svc.model_error,
            "label_map_ready": len(svc.labels) == 50,
            "label_error": svc.label_error,
            "extractor_ready": svc.extractor_ready,
            "extractor_error": svc.extractor_error,
            "device": str(getattr(svc.model, "device", "unknown")) if svc.model else None,
            "class_count": len(svc.labels),
            "feature_dimension": 134,
            "max_frames": 169,
            "max_capture_seconds": svc.settings.max_capture_seconds,
            "confidence_threshold": svc.settings.confidence_threshold,
            "iot_enabled": bool(svc.iot_enabled and not svc.settings.pre_iot_lockdown),
            "pre_iot_lockdown": svc.settings.pre_iot_lockdown,
            "iot_mapping_configured": len(svc.iot_label_commands) == 2,
            "iot_mapping_count": len(svc.iot_label_commands),
            "iot_mapping_error": svc.iot_mapping_error,
            "iot_min_confidence": svc.settings.iot_min_confidence,
            "confidence_threshold_is_calibrated": False,
            "database_path": svc.settings.database_path.as_posix() if isinstance(svc.settings.database_path, Path) else svc.settings.database_path,
            "mqtt_configured": bool(svc.settings.mqtt_host and svc.settings.mqtt_port),
        }

    @app.get("/api/vocabulary")
    async def vocabulary():
        """Return the exact INCLUDE-50 class names and stable class indices.

        These are English class glosses, not visual sign instructions or sentence translations.
        """
        svc: RuntimeServices = app.state.services
        readable = {
            "biglarge": "big / large", "cellphone": "cell phone",
            "goodmorning": "good morning", "smalllittle": "small / little",
            "storeorshop": "store / shop", "trainticket": "train ticket",
            "tshirt": "T-shirt", "youplural": "you (plural)", "i": "I",
        }
        classes = [
            {"index": int(index), "label": label, "display_name": readable.get(label, label)}
            for label, index in sorted(svc.labels.items(), key=lambda item: item[1])
        ]
        return {
            "vocabulary_name": "AI4Bharat INCLUDE-50",
            "class_count": len(classes),
            "classes": classes,
            "semantics_note": "English class glosses for isolated gesture classes; not gesture instructions or sentence translation.",
            "repository_url": "https://github.com/AI4Bharat/INCLUDE",
            "dataset_url": "https://huggingface.co/datasets/ai4bharat/INCLUDE",
        }

    @app.get("/api/reference-videos")
    async def reference_videos():
        """List locally available official INCLUDE-50 videos grouped by model class.

        Metadata is fetched separately by scripts/prepare_include_references.py. Video bytes
        remain outside the application ZIP because they are a separately distributed dataset.
        """
        svc: RuntimeServices = app.state.services
        metadata_path = svc.settings.reference_metadata_path
        video_root = svc.settings.reference_video_dir.resolve()
        rows: list[dict[str, Any]] = []
        metadata_ready = False
        if metadata_path.is_file():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("videos"), list):
                    rows = [r for r in payload["videos"] if isinstance(r, dict)]
                    metadata_ready = True
            except Exception:
                rows = []
        aliases = {
            "biglarge": ["biglarge", "big large", "large"], "cellphone": ["cellphone", "cell phone", "mobile phone"],
            "goodmorning": ["goodmorning", "good morning"], "smalllittle": ["smalllittle", "small little", "small", "little"],
            "storeorshop": ["storeorshop", "store or shop", "store", "shop"], "trainticket": ["trainticket", "train ticket"],
            "tshirt": ["tshirt", "t shirt", "t-shirt"], "youplural": ["youplural", "you plural", "you"], "i": ["i", "me"],
        }
        def norm(value: str) -> str:
            return "".join(ch.lower() for ch in value if ch.isalnum())
        by_label: dict[str, list[dict[str, str]]] = {label: [] for label in svc.labels}
        for row in rows:
            raw_label = str(row.get("label", ""))
            class_name = raw_label.split("/")[-1]
            # Dataset labels usually look like "22. Hello". Drop the leading numeric ID.
            if ". " in class_name:
                class_name = class_name.split(". ", 1)[1]
            normalized = norm(class_name)
            relative = str(row.get("video_path", "")).replace("\\", "/").lstrip("/")
            if not relative or ".." in Path(relative).parts:
                continue
            matched = None
            for model_label in svc.labels:
                accepted = {norm(model_label), *(norm(a) for a in aliases.get(model_label, []))}
                if normalized in accepted:
                    matched = model_label
                    break
            if matched is None:
                continue
            candidate = (video_root / Path(relative)).resolve()
            try:
                candidate.relative_to(video_root)
            except ValueError:
                continue
            exists = candidate.is_file()
            by_label[matched].append({"label": matched, "display_name": class_name, "category": raw_label.split("/")[0] if "/" in raw_label else "", "split": str(row.get("split", "unknown")).lower(), "include_50": row.get("include_50") is True, "video_path": relative, "available": exists, "url": f"/api/reference-video?path={relative}" if exists else ""})
        readable = {"biglarge":"big / large", "cellphone":"cell phone", "goodmorning":"good morning", "smalllittle":"small / little", "storeorshop":"store / shop", "trainticket":"train ticket", "tshirt":"T-shirt", "youplural":"you (plural)", "i":"I"}
        classes = []
        for label, index in sorted(svc.labels.items(), key=lambda item: item[1]):
            split_order = {"train": 0, "val": 1, "validation": 1, "test": 2}
            items = sorted(by_label.get(label, []), key=lambda item: (split_order.get(item.get("split", "unknown"), 9), item["video_path"]))
            classes.append({"index": int(index), "label": label, "display_name": readable.get(label, label), "video_count": len(items), "available_count": sum(bool(v["available"]) for v in items), "videos": items})
        return {"vocabulary_name":"AI4Bharat INCLUDE-50", "class_count":len(classes), "metadata_ready":metadata_ready, "metadata_path":str(metadata_path), "video_root":str(video_root), "available_video_count":sum(c["available_count"] for c in classes), "classes":classes, "message": "Reference metadata loaded. Listing all local metadata entries matching the 50 model classes, including rows not flagged in the curated subset." if metadata_ready else "Reference metadata not prepared yet. Run scripts\\prepare_include_references.py, then place/download the official videos under ISL_REFERENCE_VIDEO_DIR."}

    @app.get("/api/reference-video")
    async def serve_reference_video(path: str):
        """Serve only a file contained in the configured local reference-video directory."""
        svc: RuntimeServices = app.state.services
        root = svc.settings.reference_video_dir.resolve()
        relative = Path(unquote(path).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(status_code=400, detail="Invalid reference video path.")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid reference video path.")
        if not candidate.is_file() or candidate.suffix.lower() not in {".mp4", ".mov", ".avi", ".m4v", ".webm"}:
            raise HTTPException(status_code=404, detail="Reference video not found. Check ISL_REFERENCE_VIDEO_DIR and the INCLUDE dataset extraction.")
        return FileResponse(candidate, media_type={".mp4":"video/mp4", ".mov":"video/quicktime", ".avi":"video/x-msvideo", ".m4v":"video/mp4", ".webm":"video/webm"}.get(candidate.suffix.lower(), "application/octet-stream"), filename=candidate.name)

    @app.get("/api/history")
    async def recognition_history(limit: int = 100):
        svc: RuntimeServices = app.state.services
        svc.store.mark_timeouts(time.time())
        return {"events": svc.store.recognition_history(limit)}

    @app.get("/api/iot/history")
    async def iot_history(limit: int = 100):
        svc: RuntimeServices = app.state.services
        svc.store.mark_timeouts(time.time())
        return {"events": svc.store.iot_history(limit)}

    @app.post("/api/iot/enable")
    async def set_iot_enabled(payload: dict[str, Any]):
        svc: RuntimeServices = app.state.services
        if not isinstance(payload.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="Body must contain boolean 'enabled'.")
        if svc.settings.pre_iot_lockdown:
            # Return a visible policy response; the command path also enforces the lock.
            svc.iot_enabled = False
            return {
                "iot_enabled": False,
                "pre_iot_lockdown": True,
                "message": "IoT is locked in this pre-IoT release. No device command can be published.",
            }
        if payload["enabled"] and len(svc.iot_label_commands) != 2:
            svc.iot_enabled = False
            return {
                "iot_enabled": False,
                "pre_iot_lockdown": False,
                "message": "IoT cannot be enabled until exactly two valid INCLUDE-50 sign mappings are configured.",
                "iot_mapping_count": len(svc.iot_label_commands),
                "iot_mapping_error": svc.iot_mapping_error,
            }
        svc.iot_enabled = payload["enabled"]
        return {
            "iot_enabled": svc.iot_enabled,
            "pre_iot_lockdown": False,
            "message": "IoT command path enabled for this app session." if svc.iot_enabled else "IoT command path disabled.",
            "iot_mapping_count": len(svc.iot_label_commands),
        }

    @app.post("/api/history/reset")
    async def reset_history(payload: dict[str, Any]):
        if payload.get("confirm") is not True:
            raise HTTPException(status_code=400, detail="Send {\"confirm\":true} to reset local event history.")
        svc: RuntimeServices = app.state.services
        return svc.store.reset_history()

    @app.post("/api/history/retention")
    async def retain_history(payload: dict[str, Any]):
        if payload.get("confirm") is not True or not isinstance(payload.get("before"), str):
            raise HTTPException(status_code=400, detail="Send {\"confirm\":true,\"before\":<ISO-8601 timestamp>}.")
        from datetime import datetime, timezone
        try:
            parsed = datetime.fromisoformat(payload["before"].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone is required")
            before = parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid ISO-8601 timestamp: {exc}")
        svc: RuntimeServices = app.state.services
        return svc.store.delete_before(before)

    @app.websocket("/ws/landmark-preview")
    async def websocket_landmark_preview(websocket: WebSocket):
        """Return occasional raw MediaPipe landmarks for a live camera guide.

        This endpoint uses the same local Python MediaPipe Hands/Pose extractor as
        the recognition pipeline, so it does not depend on browser CDN/model hosting.
        Preview landmarks are display-only and never enter the classifier input.
        Frames are processed in memory and are not persisted.
        """
        await websocket.accept()
        svc: RuntimeServices = app.state.services
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="isl-preview-mediapipe")
        session_obj = None
        last_frame_at = 0.0

        async def send(payload: dict[str, Any]) -> None:
            await websocket.send_json(payload)

        try:
            if not svc.extractor_ready:
                await send({
                    "type": "error",
                    "message": svc.extractor_error or "Local MediaPipe/OpenCV extractor is unavailable.",
                })
                await websocket.close(code=1011)
                return

            loop = asyncio.get_running_loop()
            session_obj = await loop.run_in_executor(executor, svc.extractor.start_session)
            await send({
                "type": "connected",
                "detector": "local-python-mediapipe",
                "message": "Live diagnostics use the local Python MediaPipe extractor; no browser CDN is required.",
            })

            while True:
                raw_text = await websocket.receive_text()
                if len(raw_text) > max(4096, svc.settings.max_frame_bytes * 2):
                    await send({"type": "error", "message": "Diagnostic frame message exceeds the configured size limit."})
                    continue
                try:
                    message = json.loads(raw_text)
                except json.JSONDecodeError:
                    await send({"type": "error", "message": "Diagnostic messages must be valid JSON."})
                    continue
                if not isinstance(message, dict) or message.get("type") != "frame":
                    await send({"type": "error", "message": "Expected a diagnostic message of type 'frame'."})
                    continue

                # A slow diagnostic preview must never create an unbounded queue. The
                # browser normally waits for each response before sending another.
                now = time.monotonic()
                elapsed_since_preview = now - last_frame_at
                if elapsed_since_preview < 0.05:
                    # Always acknowledge throttled requests so the browser clears its
                    # in-flight flag and can retry instead of freezing the overlay.
                    retry_after_ms = max(10, int((0.05 - elapsed_since_preview) * 1000) + 5)
                    await send({"type": "throttled", "retry_after_ms": retry_after_ms,
                                "message": "Preview rate limited briefly to protect the local MediaPipe worker."})
                    continue

                encoded = message.get("jpeg_b64")
                if not isinstance(encoded, str):
                    await send({"type": "error", "message": "Diagnostic frame requires jpeg_b64."})
                    continue
                if encoded.startswith("data:image/jpeg;base64,"):
                    encoded = encoded.split(",", 1)[1]
                if len(encoded) > ((svc.settings.max_frame_bytes + 2) // 3) * 4 + 8:
                    await send({"type": "error", "message": "Diagnostic JPEG exceeds the configured size limit."})
                    continue
                try:
                    jpeg = base64.b64decode(encoded, validate=True)
                except Exception:
                    await send({"type": "error", "message": "Diagnostic JPEG must be valid base64."})
                    continue
                if not jpeg or len(jpeg) > svc.settings.max_frame_bytes:
                    await send({"type": "error", "message": "Diagnostic JPEG is empty or exceeds the configured size limit."})
                    continue

                last_frame_at = now
                started = time.perf_counter()
                try:
                    diagnostic_extract = getattr(svc.extractor, "extract_with_diagnostics", None)
                    if callable(diagnostic_extract):
                        extracted = await loop.run_in_executor(
                            executor, diagnostic_extract, jpeg, session_obj
                        )
                    else:
                        # Test/fake extractors may return diagnostic fields directly from extract().
                        extracted = await loop.run_in_executor(
                            executor, svc.extractor.extract, jpeg, session_obj
                        )
                except Exception as exc:
                    await send({"type": "frame_error", "message": f"Local landmark extraction failed: {type(exc).__name__}: {exc}"})
                    continue

                pose_points = getattr(extracted, "pose_landmarks", []) or []
                hand_points = getattr(extracted, "hands", []) or []
                width = int(getattr(extracted, "frame_width", 0) or message.get("width") or 0)
                height = int(getattr(extracted, "frame_height", 0) or message.get("height") or 0)
                pose_count = sum(
                    1 for point in pose_points[:25]
                    if isinstance(point, dict)
                    and isinstance(point.get("x"), (int, float))
                    and isinstance(point.get("y"), (int, float))
                    and float(point.get("visibility", 1.0)) >= 0.35
                )
                await send({
                    "type": "result",
                    "timestamp": message.get("timestamp", time.perf_counter() * 1000.0),
                    "width": width,
                    "height": height,
                    "poseLandmarks": pose_points,
                    "hands": hand_points,
                    "handLabels": getattr(extracted, "hand_labels", []) or [],
                    "posePointCount": pose_count,
                    "handCount": int(getattr(extracted, "hand_count", len(hand_points)) or 0),
                    "latencyMs": (time.perf_counter() - started) * 1000.0,
                    "detector": "local-python-mediapipe",
                })
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await send({"type": "error", "message": f"Diagnostic connection failed: {type(exc).__name__}: {exc}"})
            except Exception:
                pass
        finally:
            if session_obj is not None:
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        executor, svc.extractor.close_session, session_obj
                    )
                except Exception:
                    pass
            executor.shutdown(wait=False, cancel_futures=True)

    @app.websocket("/ws/capture")
    async def websocket_capture(websocket: WebSocket):
        """Record a short clip first, then extract every frame sequentially.

        This avoids real-time MediaPipe backpressure dropping most source frames. The
        model still receives the official fixed (169, 134) tensor after zero padding.
        Captured JPEGs remain in memory and are never written to disk.
        """
        await websocket.accept()
        svc: RuntimeServices = app.state.services
        send_lock = asyncio.Lock()
        capture: dict[str, Any] | None = None
        session_obj = None
        lock_acquired = False
        extractor_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="isl-mediapipe")

        async def send(message: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(message)

        async def close_session() -> None:
            nonlocal session_obj
            if session_obj is not None:
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        extractor_executor, svc.extractor.close_session, session_obj
                    )
                except Exception:
                    pass
                session_obj = None

        async def release_capture_lock() -> None:
            nonlocal lock_acquired
            if lock_acquired:
                lock_acquired = False
                if app.state.capture_lock.locked():
                    app.state.capture_lock.release()

        async def finish_capture(diagnostic_mode: bool = False) -> None:
            nonlocal capture
            if capture is None:
                await send({"type": "error", "code": "no_active_capture", "message": "Start a capture before submitting."})
                return
            current = capture
            capture = None
            current["accepting"] = False
            current["stopped_monotonic"] = current.get("stopped_monotonic") or time.monotonic()
            jpeg_frames = current["jpeg_frames"]
            received_count = len(jpeg_frames)
            duration_ms = max(0.0, (current["stopped_monotonic"] - current["started_monotonic"]) * 1000.0)
            client_drops = int(current.get("client_frames_dropped", 0))
            effective_fps = received_count / (duration_ms / 1000.0) if received_count and duration_ms > 0 else None
            features: list[np.ndarray] = []
            frame_metrics: list[dict[str, Any]] = []
            mediapipe_latencies_ms: list[float] = []
            pose_count = 0
            hand_count = 0
            hand1_count = 0
            hand2_count = 0
            both_hand_count = 0
            extraction_failures = 0
            diagnostic_override_used = False
            processing_started = time.perf_counter()
            feature_session_setup_ms: float | None = None
            fatal_error: str | None = None
            reason: str | None = None

            if received_count < svc.settings.min_capture_frames:
                reason = f"Too few camera frames captured: {received_count}; need at least {svc.settings.min_capture_frames}."
            else:
                await send({
                    "type": "processing_started",
                    "total_frames": received_count,
                    "message": "Clip recorded. Extracting landmarks from every captured frame; this may take several seconds.",
                })
                try:
                    loop = asyncio.get_running_loop()
                    setup_started = time.perf_counter()
                    session_obj = await loop.run_in_executor(extractor_executor, svc.extractor.start_session)
                    feature_session_setup_ms = (time.perf_counter() - setup_started) * 1000.0
                    for frame_index, item in enumerate(jpeg_frames, start=1):
                        frame_started = time.perf_counter()
                        try:
                            extracted = await loop.run_in_executor(
                                extractor_executor, svc.extractor.extract, item["jpeg"], session_obj
                            )
                            vec = np.asarray(extracted.features, dtype=np.float32)
                            if vec.shape != (134,) or np.isinf(vec).any():
                                raise ValueError("Feature extractor did not return a valid 134-D frame")
                            feature_row_index = len(features)
                            features.append(vec.copy())
                            pose_found = bool(extracted.pose_found)
                            pose_count += int(pose_found)
                            detected_hands = int(getattr(extracted, "hand_count", 0))
                            any_hand = detected_hands > 0
                            # Backward-compatible fallbacks keep simple fake/test extractors useful.
                            h1_found = bool(getattr(extracted, "hand1_found", detected_hands >= 1))
                            h2_found = bool(getattr(extracted, "hand2_found", detected_hands >= 2))
                            hand_count += int(any_hand)
                            hand1_count += int(h1_found)
                            hand2_count += int(h2_found)
                            both_hand_count += int(h1_found and h2_found)
                            frame_metrics.append({
                                "frame_index": frame_index - 1,
                                "feature_row_index": feature_row_index,
                                "extraction_status": "ok",
                                "pose_detected": pose_found,
                                "hand_count": detected_hands,
                                "hand1_detected": h1_found,
                                "hand2_detected": h2_found,
                            })
                        except Exception as exc:
                            extraction_failures += 1
                            frame_metrics.append({
                                "frame_index": frame_index - 1,
                                "feature_row_index": None,
                                "extraction_status": "failed",
                                "error": f"{type(exc).__name__}: {exc}"[:300],
                                "pose_detected": False,
                                "hand_count": 0,
                                "hand1_detected": False,
                                "hand2_detected": False,
                            })
                        finally:
                            mediapipe_latencies_ms.append((time.perf_counter() - frame_started) * 1000.0)
                        await send({
                            "type": "processing_progress",
                            "frames": frame_index,
                            "total_frames": received_count,
                            "features_extracted": len(features),
                            "failures": extraction_failures,
                        })
                    await close_session()
                except Exception as exc:
                    fatal_error = f"Landmark extraction failed: {type(exc).__name__}: {exc}"
                    await close_session()

            frame_count = len(features)
            pose_ratio = pose_count / frame_count if frame_count else 0.0
            hand_ratio = hand_count / frame_count if frame_count else 0.0
            hand1_ratio = hand1_count / frame_count if frame_count else 0.0
            hand2_ratio = hand2_count / frame_count if frame_count else 0.0
            both_hand_ratio = both_hand_count / frame_count if frame_count else 0.0
            median_mp = float(np.median(mediapipe_latencies_ms)) if mediapipe_latencies_ms else None
            p95_mp = float(np.percentile(mediapipe_latencies_ms, 95)) if mediapipe_latencies_ms else None
            extraction_total_ms = float(sum(mediapipe_latencies_ms)) if mediapipe_latencies_ms else None
            extraction_fps = (frame_count / (extraction_total_ms / 1000.0)) if extraction_total_ms and extraction_total_ms > 0 else None
            frame_timing = _frame_timing_summary(jpeg_frames)
            current["frame_timing"] = frame_timing
            debug_save_result = None
            if current.get("save_debug"):
                debug_summary = {
                    "capture_duration_ms": duration_ms,
                    "received_frames": received_count,
                    "extracted_feature_frames": frame_count,
                    "extraction_failures": extraction_failures,
                    "pose_ratio": pose_count / frame_count if frame_count else 0.0,
                    "any_hand_ratio": hand_count / frame_count if frame_count else 0.0,
                    "hand1_ratio": hand1_count / frame_count if frame_count else 0.0,
                    "hand2_ratio": hand2_count / frame_count if frame_count else 0.0,
                    "both_hand_ratio": both_hand_count / frame_count if frame_count else 0.0,
                    "mediapipe_p95_ms": float(np.percentile(mediapipe_latencies_ms, 95)) if mediapipe_latencies_ms else None,
                    **frame_timing,
                }
                debug_save_result = _persist_debug_capture(current, frame_metrics, features, debug_summary)
            if not fatal_error and not reason:
                if frame_count < svc.settings.min_capture_frames:
                    reason = f"Too few feature frames extracted: {frame_count}; need at least {svc.settings.min_capture_frames}."
                elif pose_ratio < svc.settings.min_pose_ratio:
                    reason = f"Pose detected in {pose_ratio:.0%} of extracted frames; provisional minimum is {svc.settings.min_pose_ratio:.0%}."
                elif hand_ratio < svc.settings.min_hand_ratio:
                    if diagnostic_mode and hand_ratio > 0.0:
                        diagnostic_override_used = True
                    else:
                        reason = f"At least one hand detected in {hand_ratio:.0%} of extracted frames; provisional minimum is {svc.settings.min_hand_ratio:.0%}."
                if reason is None and extraction_failures / max(1, extraction_failures + frame_count) > svc.settings.max_extraction_failure_ratio:
                    ratio = extraction_failures / max(1, extraction_failures + frame_count)
                    reason = f"Frame extraction failure ratio {ratio:.0%} exceeds the provisional maximum {svc.settings.max_extraction_failure_ratio:.0%}."

            base_event = {
                "capture_id": current["capture_id"], "created_at": utc_now(), "predicted_label": None,
                "confidence": None, "processing_latency_ms": None, "capture_duration_ms": duration_ms,
                "frame_count": frame_count, "frames_dropped": client_drops,
                "outcome": "invalid_capture", "reason": reason,
                "mediapipe_latency_ms": median_mp, "effective_fps": effective_fps,
                "received_frames": received_count, "extraction_failures": extraction_failures,
                "pose_ratio": pose_ratio, "hand_ratio": hand_ratio,
                "hand1_ratio": hand1_ratio, "hand2_ratio": hand2_ratio,
                "both_hand_ratio": both_hand_ratio, "mediapipe_p95_latency_ms": p95_mp,
                "mediapipe_median_latency_ms": median_mp,
                "feature_session_setup_ms": feature_session_setup_ms,
                "feature_extraction_latency_ms": extraction_total_ms,
                "feature_extraction_fps": extraction_fps,
                "diagnostic_mode": bool(diagnostic_mode),
                "diagnostic_override_used": bool(diagnostic_override_used),
                "client_timestamp_fps": frame_timing["client_timestamp_fps"],
                "frame_interval_mean_ms": frame_timing["frame_interval_mean_ms"],
                "frame_interval_median_ms": frame_timing["frame_interval_median_ms"],
                "frame_interval_p95_ms": frame_timing["frame_interval_p95_ms"],
                "frame_interval_count": frame_timing["frame_interval_count"],
                "camera_settings": current.get("camera_settings"),
                "expected_label": current.get("expected_label"),
                "debug_capture_saved": bool(debug_save_result and debug_save_result.get("saved")),
                "debug_capture_directory": debug_save_result.get("directory") if debug_save_result else None,
                "debug_capture_error": debug_save_result.get("error") if debug_save_result and not debug_save_result.get("saved") else None,
            }
            if fatal_error:
                event = {**base_event, "outcome": "error", "reason": fatal_error,
                         "processing_latency_ms": (time.perf_counter() - processing_started) * 1000.0,
                         "end_to_end_latency_ms": (time.monotonic() - current["started_monotonic"]) * 1000.0}
                _write_debug_result(event)
                svc.store.add_recognition(event)
                await send({"type": "result", "result": {**event, "status": "error"}, "iot": None})
                await release_capture_lock()
                return
            if reason or not features:
                if not reason:
                    base_event["reason"] = "No valid feature frames were extracted."
                base_event["processing_latency_ms"] = (time.perf_counter() - processing_started) * 1000.0
                base_event["end_to_end_latency_ms"] = (time.monotonic() - current["started_monotonic"]) * 1000.0
                _write_debug_result(base_event)
                svc.store.add_recognition(base_event)
                await send({"type": "result", "result": {**base_event, "status": "invalid_capture", "pose_ratio": pose_ratio, "hand_ratio": hand_ratio}, "iot": None})
                await release_capture_lock()
                return

            inference_ms = None
            try:
                sequence = np.stack(features, axis=0).astype(np.float32)
                raw, padded = preprocess_feature_sequence(sequence, max_frame_len=169, feature_dim=134)
                if raw.shape[0] != frame_count or padded.shape != (169, 134) or not np.isfinite(padded).all():
                    raise RuntimeError("Preprocessed sequence violates the finite (169,134) model contract")
                inference_started = time.perf_counter()
                prediction = await asyncio.to_thread(svc.model.predict, padded)
                inference_ms = (time.perf_counter() - inference_started) * 1000.0
                idx = int(prediction["predicted_index"])
                conf = float(prediction["confidence"])
                label_by_index = invert_label_map(svc.labels)
                if idx not in label_by_index or not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
                    raise RuntimeError("Model output contains an invalid class index or confidence")
                label = label_by_index[idx]
                processing_ms = (time.perf_counter() - processing_started) * 1000.0
                accepted = conf >= svc.settings.confidence_threshold
                outcome = "accepted" if accepted else "rejected"
                event = {
                    **base_event, "predicted_label": label, "confidence": conf,
                    "processing_latency_ms": processing_ms, "inference_latency_ms": inference_ms,
                    "end_to_end_latency_ms": (time.monotonic() - current["started_monotonic"]) * 1000.0,
                    "outcome": outcome,
                    "reason": None if accepted else f"Confidence {conf:.4f} is below the uncalibrated default threshold {svc.settings.confidence_threshold:.2f}.",
                    "device": str(prediction.get("device", "unknown")),
                }
                svc.store.add_recognition(event)
                top5_indices = prediction.get("top5_indices", []) or []
                top5_probabilities = prediction.get("top5_probabilities", []) or []
                top5_predictions = [
                    {"label": label_by_index[int(top_idx)], "confidence": float(prob)}
                    for top_idx, prob in zip(top5_indices, top5_probabilities)
                    if int(top_idx) in label_by_index and math.isfinite(float(prob)) and 0.0 <= float(prob) <= 1.0
                ]
                result = {
                    **event, "status": outcome, "pose_ratio": pose_ratio, "hand_ratio": hand_ratio,
                    "top5_indices": top5_indices,
                    "top5_probabilities": top5_probabilities,
                    "top5_predictions": top5_predictions,
                    "confidence_threshold": svc.settings.confidence_threshold,
                    "threshold_is_calibrated": False,
                }
                _write_debug_result(result)
                if diagnostic_mode:
                    iot_result = {
                        "status": "suppressed",
                        "message": "IoT command suppressed for this diagnostic-mode capture; no device action was requested.",
                    }
                else:
                    iot_result = await maybe_issue_iot_command(svc, result)
                await send({"type": "result", "result": result, "iot": iot_result})
            except Exception as exc:
                event = {
                    **base_event, "outcome": "error",
                    "reason": f"Inference/preprocessing error: {type(exc).__name__}: {exc}",
                    "processing_latency_ms": (time.perf_counter() - processing_started) * 1000.0,
                    "inference_latency_ms": inference_ms,
                    "end_to_end_latency_ms": (time.monotonic() - current["started_monotonic"]) * 1000.0,
                }
                _write_debug_result(event)
                svc.store.add_recognition(event)
                await send({"type": "result", "result": {**event, "status": "error"}, "iot": None})
            finally:
                await release_capture_lock()

        try:
            await send({
                "type": "connected", "model_ready": svc.model_ready, "model_error": svc.model_error,
                "extractor_ready": svc.extractor_ready, "extractor_error": svc.extractor_error,
                "protocol_version": "1.1-record-then-extract",
            })
            while True:
                packet = await websocket.receive()
                if packet.get("type") == "websocket.disconnect":
                    break
                raw_text = packet.get("text")
                if raw_text is None:
                    await send({"type": "error", "code": "text_json_required", "message": "Send text JSON messages; binary WebSocket frames are not supported by protocol v1.1."})
                    continue
                if len(raw_text) > max(4096, svc.settings.max_frame_bytes * 2):
                    await send({"type": "error", "code": "message_too_large", "message": "WebSocket message exceeds configured limit."})
                    continue
                try:
                    message = json.loads(raw_text)
                except json.JSONDecodeError:
                    await send({"type": "error", "code": "invalid_json", "message": "WebSocket messages must be valid JSON."})
                    continue
                if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                    await send({"type": "error", "code": "invalid_message", "message": "Each message must be a JSON object with a string 'type'."})
                    continue
                kind = message["type"]

                if kind == "start":
                    if capture is not None:
                        await send({"type": "error", "code": "capture_already_active", "message": "Submit or cancel the active capture first."})
                        continue
                    if not svc.model_ready:
                        await send({"type": "error", "code": "model_unavailable", "message": svc.model_error or svc.label_error or "Official model is unavailable."})
                        continue
                    if not svc.extractor_ready:
                        await send({"type": "error", "code": "extractor_unavailable", "message": svc.extractor_error or "MediaPipe extractor is unavailable."})
                        continue
                    if app.state.capture_lock.locked():
                        await send({"type": "error", "code": "another_capture_active", "message": "Only one capture/inference session is allowed at a time."})
                        continue
                    await app.state.capture_lock.acquire()
                    lock_acquired = True
                    camera_settings = _safe_camera_settings(message.get("camera_settings"))
                    capture = {
                        "capture_id": str(uuid.uuid4()), "jpeg_frames": [], "frames_dropped": 0,
                        "extraction_failures": 0, "started_monotonic": time.monotonic(),
                        "started_epoch": time.time(), "accepting": True, "last_received_timestamp": None,
                        "frames_received": 0, "client_frames_dropped": 0,
                        "camera_settings": camera_settings,
                    }
                    await send({
                        "type": "capture_started", "capture_id": capture["capture_id"],
                        "max_frames": 169, "minimum_frames": svc.settings.min_capture_frames,
                        "max_capture_seconds": svc.settings.max_capture_seconds,
                    })

                elif kind == "frame":
                    if capture is None:
                        await send({"type": "error", "code": "no_active_capture", "message": "Start capture before sending frames."})
                        continue
                    if not capture["accepting"]:
                        await send({"type": "frame_limit_reached", "message": "Capture window is closed; submit the recorded clip."})
                        continue
                    elapsed = time.monotonic() - capture["started_monotonic"]
                    if elapsed >= svc.settings.max_capture_seconds:
                        capture["accepting"] = False
                        await send({"type": "time_limit_reached", "max_capture_seconds": svc.settings.max_capture_seconds,
                                    "message": f"Capture reached the {svc.settings.max_capture_seconds:.1f}-second maximum."})
                        continue
                    if len(capture["jpeg_frames"]) >= 169:
                        capture["accepting"] = False
                        await send({"type": "frame_limit_reached", "message": "169 captured frames reached. Submit this capture."})
                        continue
                    b64 = message.get("jpeg_b64")
                    timestamp = message.get("timestamp")
                    if not isinstance(b64, str) or not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or not math.isfinite(float(timestamp)):
                        await send({"type": "error", "code": "invalid_frame_message", "message": "Frame needs numeric timestamp and JPEG base64 string."})
                        continue
                    now_epoch = time.time()
                    if float(timestamp) <= 0 or float(timestamp) > now_epoch + 5 or now_epoch - float(timestamp) > 120:
                        await send({"type": "error", "code": "invalid_frame_timestamp", "message": "Frame timestamp must be recent Unix seconds (within 120 seconds, no more than 5 seconds in the future)."})
                        continue
                    previous_ts = capture.get("last_received_timestamp")
                    if previous_ts is not None and float(timestamp) <= previous_ts:
                        await send({"type": "error", "code": "non_monotonic_frame_timestamp", "message": "Frame timestamps must increase within one capture; this frame was rejected."})
                        continue
                    if b64.startswith("data:image/jpeg;base64,"):
                        b64 = b64.split(",", 1)[1]
                    if len(b64) > ((svc.settings.max_frame_bytes + 2) // 3) * 4 + 8:
                        await send({"type": "error", "code": "frame_too_large", "message": "JPEG frame exceeds configured maximum size."})
                        continue
                    try:
                        jpeg = base64.b64decode(b64, validate=True)
                    except Exception:
                        await send({"type": "error", "code": "invalid_base64", "message": "Frame JPEG must be valid base64."})
                        continue
                    if not jpeg or len(jpeg) > svc.settings.max_frame_bytes:
                        await send({"type": "error", "code": "frame_too_large", "message": "Decoded JPEG is empty or exceeds configured size."})
                        continue
                    capture["jpeg_frames"].append({"jpeg": jpeg, "timestamp": float(timestamp)})
                    capture["frames_received"] = len(capture["jpeg_frames"])
                    capture["last_received_timestamp"] = float(timestamp)
                    await send({
                        "type": "progress", "phase": "recording", "capture_seconds": elapsed,
                        "frames": len(capture["jpeg_frames"]), "max_frames": 169,
                        "queue_dropped": 0, "max_capture_seconds": svc.settings.max_capture_seconds,
                    })
                    if len(capture["jpeg_frames"]) == 169:
                        capture["accepting"] = False
                        await send({"type": "frame_limit_reached", "message": "169 captured frames reached. Submit this capture."})

                elif kind == "stop":
                    client_drops = message.get("client_frames_dropped", 0)
                    if isinstance(client_drops, bool) or not isinstance(client_drops, int) or client_drops < 0 or client_drops > 100000:
                        await send({"type": "error", "code": "invalid_drop_count", "message": "client_frames_dropped must be a non-negative integer <= 100000."})
                        continue
                    diagnostic_mode = message.get("diagnostic_mode", False)
                    if not isinstance(diagnostic_mode, bool):
                        await send({"type": "error", "code": "invalid_diagnostic_mode", "message": "diagnostic_mode must be a boolean."})
                        continue
                    save_debug = message.get("save_debug", False)
                    if not isinstance(save_debug, bool):
                        await send({"type": "error", "code": "invalid_debug_save", "message": "save_debug must be a boolean."})
                        continue
                    expected_label = message.get("expected_label")
                    if expected_label is not None and (not isinstance(expected_label, str) or len(expected_label) > 64):
                        await send({"type": "error", "code": "invalid_expected_label", "message": "expected_label must be a short string or null."})
                        continue
                    if capture is not None:
                        capture["client_frames_dropped"] = client_drops
                        capture["save_debug"] = save_debug
                        capture["expected_label"] = expected_label
                        capture["camera_settings"] = _safe_camera_settings(message.get("camera_settings")) or capture.get("camera_settings")
                        capture["stopped_monotonic"] = time.monotonic()
                    await finish_capture(diagnostic_mode=diagnostic_mode)

                elif kind == "cancel":
                    if capture is None:
                        await send({"type": "cancelled", "message": "No capture was active."})
                        continue
                    current = capture
                    capture = None
                    duration_ms = max(0.0, (time.monotonic() - current["started_monotonic"]) * 1000.0)
                    svc.store.add_recognition({
                        "capture_id": current["capture_id"], "created_at": utc_now(), "frame_count": 0,
                        "frames_dropped": int(current.get("client_frames_dropped", 0)), "capture_duration_ms": duration_ms,
                        "received_frames": len(current["jpeg_frames"]), "extraction_failures": 0,
                        "end_to_end_latency_ms": duration_ms, "outcome": "cancelled",
                        "reason": "User cancelled capture; no inference or IoT command was run.",
                    })
                    await close_session()
                    await release_capture_lock()
                    await send({"type": "cancelled", "capture_id": current["capture_id"],
                                "discarded_frames": len(current["jpeg_frames"]),
                                "message": "Capture cancelled; no inference or command was run."})

                elif kind == "ping":
                    await send({"type": "pong", "timestamp": time.time()})
                else:
                    await send({"type": "error", "code": "unknown_message_type", "message": f"Unsupported message type: {kind}"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await send({"type": "error", "code": "server_error", "message": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
        finally:
            if capture is not None:
                current = capture
                duration_ms = max(0.0, (time.monotonic() - current["started_monotonic"]) * 1000.0)
                try:
                    svc.store.add_recognition({
                        "capture_id": current["capture_id"], "created_at": utc_now(), "frame_count": 0,
                        "frames_dropped": int(current.get("client_frames_dropped", 0)), "capture_duration_ms": duration_ms,
                        "received_frames": len(current["jpeg_frames"]), "extraction_failures": 0,
                        "end_to_end_latency_ms": duration_ms, "outcome": "cancelled",
                        "reason": "WebSocket disconnected before submission; no inference or IoT command was run.",
                    })
                except Exception:
                    pass
                capture = None
            await close_session()
            await release_capture_lock()
            extractor_executor.shutdown(wait=True, cancel_futures=True)

    return app


async def maybe_issue_iot_command(svc: RuntimeServices, result: dict[str, Any]) -> dict[str, Any] | None:
    label = result.get("predicted_label")
    confidence = result.get("confidence")
    capture_id = result.get("capture_id")
    # Defense in depth: pre-IoT release lockdown takes precedence over both the
    # browser control and the process-local enabled flag.
    if svc.settings.pre_iot_lockdown:
        svc.iot_enabled = False
        return {"status": "suppressed", "message": "Pre-IoT lockdown is active. No device command was published."}
    if not svc.iot_enabled:
        return {"status": "disabled", "message": "IoT is OFF. No command was published."}
    if result.get("outcome") != "accepted" or result.get("status") != "accepted":
        return {"status": "rejected", "message": "Capture result was not accepted; no command was published."}
    if label not in svc.iot_label_commands:
        return {"status": "not_mapped", "message": "This accepted label has no approved IoT mapping."}
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        return {"status": "rejected", "message": "IoT confidence is missing or invalid; no command was published."}
    if not math.isfinite(score) or score < svc.settings.iot_min_confidence:
        return {
            "status": "rejected",
            "message": f"IoT confidence gate failed (requires {svc.settings.iot_min_confidence:.2f}); no command was published.",
        }
    now = time.time()
    if now - svc.last_iot_command_epoch < svc.settings.iot_cooldown_seconds:
        return {"status": "cooldown", "message": f"IoT cooldown active ({svc.settings.iot_cooldown_seconds:g} seconds). No command was published."}

    command = build_command(
        capture_id=str(capture_id), source_label=str(label), confidence=score,
        label_commands=svc.iot_label_commands, now=now, ttl_seconds=10.0,
        min_confidence=svc.settings.iot_min_confidence,
    )
    deadline = now + svc.settings.mqtt_ack_timeout_seconds
    # Register before publish so an immediate subscriber acknowledgement is not lost.
    inserted = svc.store.add_iot_event({
        "command_id": command["command_id"], "capture_id": capture_id, "mapped_command": command["command"],
        "source_label": label, "confidence": confidence, "created_at": utc_now(), "ack_deadline": deadline,
        "publish_status": "publishing", "ack_status": "pending", "command_json": command,
    })
    if not inserted:
        return {
            "status": "duplicate_capture",
            "message": "This capture has already created an IoT command. No second command was published.",
        }
    svc.last_iot_command_epoch = now
    if svc.mqtt is None:
        svc.mqtt = MQTTGateway(
            svc.settings,
            on_ack=lambda cid, status, detail: svc.store.update_ack(cid, status, detail),
            on_publish_attempt=lambda cid: svc.store.mark_publish_attempt(cid),
        )
    success, detail = await asyncio.to_thread(svc.mqtt.publish_command, command)
    svc.store.update_publish(command["command_id"], success, detail)
    return {
        "command_id": command["command_id"],
        "command": command["command"],
        "status": "pending" if success else "publish_failed",
        "publish_status": "published" if success else "failed",
        "message": detail,
    }


app = create_app()
