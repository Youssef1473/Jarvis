"""Starlette and WebSocket server powering the Jarvis 2.0 Holographic Web HUD."""
import asyncio
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional, Set
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import anyio
import uvicorn
import psutil

from jarvis.skills.os_control import get_raw_battery_info

logger = logging.getLogger("Jarvis.UI")

class WorkstationMetricsCollector:
    """Collects system vitals via psutil and native NVML bindings strictly off the event loop."""

    _nvml_initialized: bool = False
    _device_handle: Any = None
    _nvml_module: Any = None
    _nvml_lock: threading.Lock = threading.Lock()

    _DEFAULT_GPU_METRICS: Dict[str, Any] = {
        "gpu_name": "NVIDIA GeForce RTX 3050 Laptop GPU",
        "gpu_load": 0,
        "vram_used_mb": 0,
        "vram_total_mb": 4096,
        "gpu_temp_c": 0,
    }

    @classmethod
    def _init_nvml(cls) -> bool:
        """Initializes NVML and acquires device handle 0 inside a thread-safe singleton gate."""
        with cls._nvml_lock:
            if cls._nvml_initialized and cls._device_handle is not None:
                return True

            try:
                import pynvml
                cls._nvml_module = pynvml
                cls._nvml_module.nvmlInit()
                cls._device_handle = cls._nvml_module.nvmlDeviceGetHandleByIndex(0)
                cls._nvml_initialized = True
                return True
            except Exception as exc:
                cls._nvml_initialized = False
                cls._device_handle = None
                cls._nvml_module = None
                logger.debug("Native NVML initialization bypassed (driver or module unavailable): %s", exc)
                return False

    @classmethod
    def _get_gpu_metrics_native(cls) -> Dict[str, Any]:
        """Polls GPU hardware states natively via pynvml bindings without subprocess forks."""
        if not cls._init_nvml():
            # Fallback to secondary watchdog query if available
            try:
                from jarvis.skills.system_watchdog import _query_gpu_telemetry
                cached = _query_gpu_telemetry()
                if cached:
                    return {
                        "gpu_name": cached.get("name", cls._DEFAULT_GPU_METRICS["gpu_name"]),
                        "gpu_load": cached.get("load_pct", 0),
                        "vram_used_mb": cached.get("vram_used", 0),
                        "vram_total_mb": cached.get("vram_total", 4096),
                        "gpu_temp_c": cached.get("temp_c", 0),
                    }
            except Exception:
                pass
            return dict(cls._DEFAULT_GPU_METRICS)

        try:
            handle = cls._device_handle
            nvml = cls._nvml_module

            raw_name = nvml.nvmlDeviceGetName(handle)
            gpu_name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)

            util_rates = nvml.nvmlDeviceGetUtilizationRates(handle)
            gpu_load = int(util_rates.gpu)

            mem_info = nvml.nvmlDeviceGetMemoryInfo(handle)
            vram_used_mb = int(mem_info.used / (1024 * 1024))
            vram_total_mb = int(mem_info.total / (1024 * 1024))

            gpu_temp_c = int(nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU))

            return {
                "gpu_name": gpu_name,
                "gpu_load": gpu_load,
                "vram_used_mb": vram_used_mb,
                "vram_total_mb": vram_total_mb,
                "gpu_temp_c": gpu_temp_c,
            }
        except Exception as exc:
            logger.debug("NVML hardware poll failed; resetting handle: %s", exc)
            with cls._nvml_lock:
                cls._nvml_initialized = False
                cls._device_handle = None
            return dict(cls._DEFAULT_GPU_METRICS)

    @classmethod
    def shutdown_nvml(cls):
        """Cleanly finalizes the NVML driver session during host termination."""
        with cls._nvml_lock:
            if cls._nvml_initialized and cls._nvml_module is not None:
                try:
                    cls._nvml_module.nvmlShutdown()
                except Exception:
                    pass
                cls._nvml_initialized = False
                cls._device_handle = None
                cls._nvml_module = None

    @staticmethod
    def sample_metrics_sync(orchestrator: Any = None) -> Dict[str, Any]:
        """Synchronous vitals collection combining psutil host memory/CPU with native NVML GPU telemetry."""
        bat = get_raw_battery_info()
        cpu_val = round(psutil.cpu_percent(interval=None), 1)

        vmem = psutil.virtual_memory()
        ram_pct = round(vmem.percent, 1)
        ram_used_gb = round(vmem.used / (1024 ** 3), 1)
        ram_total_gb = round(vmem.total / (1024 ** 3), 1)
        ram_used_mb = int(vmem.used / (1024 * 1024))
        ram_total_mb = int(vmem.total / (1024 * 1024))

        gpu_data = WorkstationMetricsCollector._get_gpu_metrics_native()

        state = "idle"
        active_model = "qwen/qwen3.8-27b"
        tier = "TRI-TIER HYBRID"
        provider = "hybrid"
        is_muted = False
        specialist_status = {"gemini": "ACTIVE", "groq": "ACTIVE", "grok": "METERED / OFF", "claude": "METERED / OFF"}
        latencies = {"ollama": 0.0, "groq": 0.0, "gemini": 0.0}
        memory_label = "TIER 2 (ACTIVE)"

        if orchestrator is not None:
            if hasattr(orchestrator, "is_muted") and callable(orchestrator.is_muted):
                try:
                    is_muted = bool(orchestrator.is_muted())
                except Exception:
                    is_muted = False

            if hasattr(orchestrator, "state"):
                state = str(getattr(orchestrator, "state", "idle"))

            if hasattr(orchestrator, "brain"):
                b = orchestrator.brain
                p = getattr(b, "provider", "hybrid")
                provider = p
                tier = "TRI-TIER HYBRID" if p in ("hybrid", "auto") else ("LOCAL" if p == "ollama" else "CLOUD")
                active_model = str(getattr(b, "openai_model", getattr(b, "provider", active_model)))
                status_provider = getattr(b, "specialist_status", None)
                if callable(status_provider):
                    try:
                        specialist_status = status_provider()
                    except Exception:
                        pass
                lat_getter = getattr(b, "get_latencies", None)
                if callable(lat_getter):
                    try:
                        latencies = lat_getter()
                    except Exception:
                        pass
                if hasattr(b, "memory"):
                    try:
                        facts = b.memory.get_all_facts()
                        memory_label = f"{len(facts)} FACTS STORED"
                    except Exception:
                        pass

        timer_count = 0
        try:
            from jarvis.skills.timers import _active_timers, _timer_lock
            with _timer_lock:
                timer_count = len(_active_timers)
        except Exception:
            pass

        return {
            # Single-character keys required by Council Telemetry Directive
            "c": cpu_val,
            "rp": ram_pct,
            "ru": ram_used_mb,
            "rt": ram_total_mb,
            "gp": gpu_data["gpu_load"],
            "gl": gpu_data["gpu_load"],
            "vu": gpu_data["vram_used_mb"],
            "vt": gpu_data["vram_total_mb"],
            "gt": gpu_data["gpu_temp_c"],
            "st": state,
            "md": active_model,
            "m": 1 if is_muted else 0,
            "ts": int(time.time() * 1000),
            # Backwards-compatible fields for existing test assertions & components
            "cpu": cpu_val,
            "ram_pct": ram_pct,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "gpu": gpu_data["gpu_name"],
            "gpu_name": gpu_data["gpu_name"],
            "gpu_load": gpu_data["gpu_load"],
            "gpu_temp": gpu_data["gpu_temp_c"],
            "vram_used": f"{gpu_data['vram_used_mb']} MB",
            "vram_used_mb": gpu_data["vram_used_mb"],
            "vram_total_mb": gpu_data["vram_total_mb"],
            "model": active_model,
            "tier": tier,
            "provider": provider,
            "state": state,
            "specialists": specialist_status,
            "active_timers": timer_count,
            "latencies": latencies,
            "memory_label": memory_label,
            "is_muted": is_muted,
            "battery": bat.get("percent", 100),
            "charging": bat.get("charging", False),
            "has_battery": bat.get("has_battery", False),
            "timestamp": time.strftime("%H:%M:%S"),
        }


class TelemetryConnectionManager:
    """Manages active HUD WebSocket connections with an 8-subscriber ceiling and lossy Queue(maxsize=1)."""

    def __init__(self, max_clients: int = 8):
        self.max_clients = max_clients
        self._clients: Dict[WebSocket, asyncio.Queue] = {}
        self._sender_tasks: Dict[WebSocket, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def connected_clients(self) -> Set[WebSocket]:
        return set(self._clients.keys())

    async def connect(self, websocket: WebSocket) -> bool:
        """Enforces 8-subscriber connection ceiling and binds an isolated Queue(maxsize=1)."""
        async with self._lock:
            if len(self._clients) >= self.max_clients:
                logger.warning("HUD WebSocket connection rejected: max client ceiling (%d) reached.", self.max_clients)
                await websocket.close(code=1008, reason="Max subscriber ceiling reached")
                return False

            await websocket.accept()
            queue: asyncio.Queue = asyncio.Queue(maxsize=1)
            self._clients[websocket] = queue
            task = asyncio.create_task(self._client_sender_worker(websocket, queue))
            self._sender_tasks[websocket] = task
            logger.info("HUD WebSocket client connected (Total: %d)", len(self._clients))
            return True

    async def disconnect(self, websocket: WebSocket):
        """Safely cleans up subscriber queue, cancels sender task, and releases socket."""
        async with self._lock:
            if websocket in self._sender_tasks:
                task = self._sender_tasks.pop(websocket)
                task.cancel()
            self._clients.pop(websocket, None)
            logger.info("HUD WebSocket client disconnected (Total: %d)", len(self._clients))

    async def _client_sender_worker(self, websocket: WebSocket, queue: asyncio.Queue):
        """Dedicated coroutine per client: drains Queue(maxsize=1) and writes to socket."""
        try:
            while True:
                payload_str = await queue.get()
                await websocket.send_text(payload_str)
                queue.task_done()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.debug("Client sender worker terminated: %s", e)
        finally:
            asyncio.create_task(self.disconnect(websocket))

    def broadcast_telemetry(self, raw_data: Dict[str, Any]):
        """Non-blocking fanout with non-blocking queue.get_nowait() to drop stale frames under backpressure."""
        if not self._clients:
            return

        payload = {
            "type": "telemetry",
            "t": "telem",
            "c": raw_data.get("c", raw_data.get("cpu", 0.0)),
            "rp": raw_data.get("rp", raw_data.get("ram_pct", 0.0)),
            "ru": raw_data.get("ru", raw_data.get("ram_used_mb", 0)),
            "rt": raw_data.get("rt", raw_data.get("ram_total_mb", 16384)),
            "gp": raw_data.get("gp", raw_data.get("gpu_load", 0)),
            "gl": raw_data.get("gl", raw_data.get("gpu_load", 0)),
            "vu": raw_data.get("vu", raw_data.get("vram_used_mb", 0)),
            "vt": raw_data.get("vt", raw_data.get("vram_total_mb", 4096)),
            "gt": raw_data.get("gt", raw_data.get("gpu_temp_c", 0)),
            "st": raw_data.get("st", raw_data.get("state", "idle")),
            "md": raw_data.get("md", raw_data.get("model", "qwen3.8-27b")),
            "m": raw_data.get("m", 0),
            "ts": raw_data.get("ts", int(time.time() * 1000)),
            # Legacy fields for existing test assertions & components
            "cpu": raw_data.get("cpu", raw_data.get("c", 0.0)),
            "ram_pct": raw_data.get("ram_pct", raw_data.get("rp", 0.0)),
            "ram_used_gb": raw_data.get("ram_used_gb", round(raw_data.get("ru", 0) / 1024, 1)),
            "ram_total_gb": raw_data.get("ram_total_gb", round(raw_data.get("rt", 16384) / 1024, 1)),
            "gpu": raw_data.get("gpu", raw_data.get("gpu_name", "RTX 3050")),
            "timestamp": raw_data.get("timestamp", time.strftime("%H:%M:%S")),
        }
        minified_str = json.dumps(payload, separators=(",", ":"))

        for queue in list(self._clients.values()):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(minified_str)
            except asyncio.QueueFull:
                pass

    async def broadcast_direct(self, message_str: str):
        """Broadcasts discrete event message directly to all connected clients."""
        dead = []
        for ws in list(self._clients.keys()):
            try:
                await ws.send_text(message_str)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


class JarvisUIService:
    """Manages the lifecycle of the Starlette HUD server and WebSocket broadcasting."""

    _instance: Optional["JarvisUIService"] = None

    def __init__(self, orchestrator: Any = None, host: str = "127.0.0.1", port: int = 20129):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.telemetry_manager = TelemetryConnectionManager(max_clients=8)
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.template_path = Path(__file__).parent / "templates" / "hud.html"
        JarvisUIService._instance = self
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

    @property
    def connected_clients(self) -> Set[WebSocket]:
        return self.telemetry_manager.connected_clients

    @classmethod
    def get_instance(cls) -> Optional["JarvisUIService"]:
        return cls._instance

    def _create_app(self) -> Starlette:
        routes = [
            Route("/", endpoint=self._handle_index, methods=["GET"]),
            Route("/api/status", endpoint=self._handle_status, methods=["GET"]),
            Route("/api/command", endpoint=self._handle_command, methods=["POST"]),
            Route("/api/action", endpoint=self._handle_action, methods=["POST"]),
            Route("/api/mute", endpoint=self._handle_mute, methods=["POST"]),
            Route("/api/notes", endpoint=self._handle_notes, methods=["GET"]),
            Route("/api/timers", endpoint=self._handle_timers, methods=["GET"]),
            Route("/api/documents/upload", endpoint=self._handle_documents_upload, methods=["POST"]),
            Route("/api/documents/staged", endpoint=self._handle_staged_documents, methods=["GET"]),
            Route("/api/calendar", endpoint=self._handle_calendar, methods=["GET"]),
            Route("/api/calendar/delete", endpoint=self._handle_calendar_delete, methods=["POST"]),
            Route("/api/calendar/event/delete", endpoint=self._handle_calendar_event_delete, methods=["POST"]),
            Route("/api/calendar/clear", endpoint=self._handle_calendar_clear, methods=["POST"]),
            Route("/api/photos", endpoint=self._handle_photos_list, methods=["GET"]),
            Route("/api/photos/{photo_id}", endpoint=self._handle_photo_stream, methods=["GET"]),
            WebSocketRoute("/ws", endpoint=self._handle_websocket),
            WebSocketRoute("/api/telemetry", endpoint=self._handle_websocket),
        ]
        return Starlette(routes=routes)

    async def _handle_photos_list(self, request):
        """Lists archived photos from SQLite memory for HUD photo matrix."""
        import sqlite3
        from jarvis.core.skills.photo_manager import DB_PATH
        try:
            def _query_photos():
                conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT photo_id, file_name, dimensions, file_size, caption, created_at
                    FROM photos
                    ORDER BY created_at DESC
                    LIMIT 60
                """)
                rows = [dict(r) for r in cur.fetchall()]
                conn.close()
                return rows

            photos = await anyio.to_thread.run_sync(_query_photos)
            return JSONResponse({"photos": photos})
        except Exception as e:
            logger.error("Error retrieving photos for HUD: %s", e)
            return JSONResponse({"photos": [], "error": str(e)})

    async def _handle_photo_stream(self, request):
        """Zero-RAM non-blocking photo streamer with ETag 304 caching."""
        import re
        photo_id = request.path_params.get("photo_id", "").strip()
        if not re.match(r"^[a-f0-9]{16,64}(\.jpg)?$", photo_id):
            return Response("Invalid photo ID format.", status_code=400)

        from jarvis.core.skills.photo_manager import PHOTOS_DIR
        target_name = photo_id if photo_id.endswith(".jpg") else f"{photo_id}.jpg"
        target_path = (PHOTOS_DIR / target_name).resolve()

        if not target_path.is_relative_to(PHOTOS_DIR.resolve()):
            return Response("Directory traversal forbidden.", status_code=403)

        exists = await anyio.to_thread.run_sync(target_path.is_file)
        if not exists:
            return Response("Photo not found.", status_code=404)

        stat = await anyio.to_thread.run_sync(target_path.stat)
        etag = f'"{stat.st_mtime_ns}-{stat.st_size}"'

        # Check browser ETag cache for 304 Not Modified
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=86400, immutable"})

        async def _chunk_reader(path: Path, chunk_size: int = 65536):
            def _read_chunk(f):
                return f.read(chunk_size)
            file_obj = await anyio.to_thread.run_sync(lambda: open(path, "rb"))
            try:
                while True:
                    chunk = await anyio.to_thread.run_sync(_read_chunk, file_obj)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await anyio.to_thread.run_sync(file_obj.close)

        headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Length": str(stat.st_size),
        }
        return StreamingResponse(_chunk_reader(target_path), media_type="image/jpeg", headers=headers)

    async def _handle_index(self, request):
        if not self.template_path.exists():
            return HTMLResponse("<h3>HUD template not found.</h3>", status_code=404)
        html_content = self.template_path.read_text(encoding="utf-8")
        return HTMLResponse(html_content)

    async def _handle_status(self, request):
        data = self._gather_telemetry()
        return JSONResponse(data)

    async def _handle_command(self, request):
        try:
            body = await request.json()
            cmd_text = body.get("command", "").strip()
        except Exception:
            cmd_text = ""

        if not cmd_text:
            return JSONResponse({"error": "Empty command"}, status_code=400)

        # Dispatch command asynchronously to orchestrator (orchestrator broadcasts message to all clients)
        if self.orchestrator:
            threading.Thread(target=self.orchestrator.handle_command, args=(cmd_text,), daemon=True).start()
            return JSONResponse({"status": "queued", "command": cmd_text})

        return JSONResponse({"error": "Orchestrator not connected"}, status_code=503)

    async def _handle_action(self, request):
        try:
            body = await request.json()
            action = body.get("action", "")
        except Exception:
            action = ""

        if action == "set_volume":
            from jarvis.skills.os_control import adjust_volume
            percent = int(body.get("percent", 50))
            adjust_volume(action="set", percent=percent)
            self.broadcast_message("jarvis", f"Master volume set to {percent}%, Sir.")
            return JSONResponse({"status": "success", "percent": percent})

        elif action == "toggle_mute":
            if self.orchestrator:
                is_muted = self.orchestrator.toggle_mute()
                self.broadcast_telemetry({"is_muted": is_muted})
                return JSONResponse({"is_muted": is_muted})

        return JSONResponse({"error": f"Unknown action '{action}'"}, status_code=400)

    async def _handle_mute(self, request):
        if self.orchestrator:
            is_muted = self.orchestrator.toggle_mute()
            self.broadcast_telemetry({"is_muted": is_muted})
            return JSONResponse({"is_muted": is_muted})
        return JSONResponse({"error": "Orchestrator not connected"}, status_code=503)

    async def _handle_notes(self, request):
        """Returns recent captured notes from data/notes/notes.md for HUD drawer."""
        notes_path = Path("data/notes/notes.md")
        if not notes_path.exists():
            return JSONResponse({"notes": []})
        try:
            raw = notes_path.read_text(encoding="utf-8")
            blocks = [b.strip() for b in raw.split("### ") if b.strip() and not b.startswith("# Jarvis")]
            notes_list = []
            for block in reversed(blocks[-15:]):
                lines = block.splitlines()
                header = lines[0].strip() if lines else "General"
                parts = header.split(" | ")
                dt_str = parts[0].strip() if len(parts) > 1 else header
                cat = parts[1].strip() if len(parts) > 1 else "General"
                body = " ".join([l.lstrip("-•* ").strip() for l in lines[1:] if l.strip()])
                notes_list.append({"timestamp": dt_str, "category": cat, "content": body})
            return JSONResponse({"notes": notes_list})
        except Exception as e:
            logger.warning(f"Error reading notes for HUD: {e}")
            return JSONResponse({"error": str(e), "notes": []})

    async def _handle_timers(self, request):
        """Returns live countdown timers for HUD drawer."""
        try:
            from jarvis.skills.timers import _active_timers, _timer_lock
            now = time.time()
            timers_list = []
            with _timer_lock:
                for t_info in _active_timers.values():
                    rem = max(0, int(t_info.get("end_time", now) - now))
                    timers_list.append({
                        "id": t_info.get("id", ""),
                        "label": t_info.get("label", "Timer"),
                        "duration": t_info.get("duration", 0),
                        "remaining": rem,
                    })
            return JSONResponse({"timers": timers_list})
        except Exception as e:
            logger.warning(f"Error reading timers for HUD: {e}")
            return JSONResponse({"timers": []})

    async def _handle_documents_upload(self, request):
        """Receives a PDF document from the file explorer upload dialog and stages it."""
        try:
            form = await request.form()
            file_field = form.get("file")
            if not file_field:
                return JSONResponse({"error": "No file uploaded"}, status_code=400)

            filename = getattr(file_field, "filename", "uploaded_doc.pdf")
            if not filename.lower().endswith(".pdf"):
                return JSONResponse({"error": "Only PDF documents are supported currently"}, status_code=400)

            base_dir = Path(__file__).resolve().parent.parent.parent
            intake_dir = base_dir / "data" / "intake"
            intake_dir.mkdir(parents=True, exist_ok=True)

            target_path = intake_dir / filename
            content = await file_field.read()
            target_path.write_bytes(content)

            import hashlib
            file_hash = hashlib.sha256(content).hexdigest()
            file_size = len(content)

            doc_info = {"id": 0, "filename": filename, "status": "PENDING_CONTEXT"}
            if self.orchestrator and hasattr(self.orchestrator, "memory"):
                doc_info = self.orchestrator.memory.stage_document(
                    filename=filename,
                    file_path=str(target_path),
                    file_hash=file_hash,
                    file_size=file_size
                )

            # Broadcast WebSocket notification
            self.broadcast({
                "type": "document_staged",
                "filename": filename,
                "id": doc_info.get("id"),
                "size_kb": round(file_size / 1024, 1),
                "message": f"Staged '{filename}'. Standing by for Sir's briefing."
            })

            if self.orchestrator and hasattr(self.orchestrator, "sfx"):
                try:
                    self.orchestrator.sfx.play_alert()
                except Exception:
                    pass

            return JSONResponse({
                "status": "staged",
                "id": doc_info.get("id"),
                "filename": filename,
                "size_kb": round(file_size / 1024, 1),
                "message": f"Document '{filename}' staged successfully. Awaiting your briefing, Sir."
            })
        except Exception as e:
            logger.error(f"Error handling document upload: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _handle_staged_documents(self, request):
        """Returns all documents waiting in the staged queue."""
        if self.orchestrator and hasattr(self.orchestrator, "memory"):
            staged = self.orchestrator.memory.get_staged_documents(status="PENDING_CONTEXT")
            return JSONResponse({"staged": staged})
        return JSONResponse({"staged": []})

    async def _handle_calendar(self, request):
        """Returns upcoming calendar appointments, grouped schedule matrix, and saved calendar documents."""
        try:
            mem = None
            if self.orchestrator and hasattr(self.orchestrator, "memory"):
                mem = self.orchestrator.memory
            else:
                from jarvis.skills.calendar_manager import _get_memory
                mem = _get_memory()

            events = mem.get_calendar_events(include_cancelled=False) if mem else []
            saved_cals = mem.get_saved_calendars() if mem and hasattr(mem, "get_saved_calendars") else []

            # Group events chronologically by date
            from collections import defaultdict
            import re
            import datetime
            grouped = defaultdict(list)
            for e in events:
                st = e.get("start_time", "")
                m = re.match(r"^(\d{4}-\d{2}-\d{2})", st)
                if m:
                    d_str = m.group(1)
                    try:
                        dt = datetime.datetime.strptime(d_str, "%Y-%m-%d")
                        day_name = dt.strftime("%A")
                        day_formatted = dt.strftime("%A, %B %d")
                    except Exception:
                        day_name = "Other"
                        day_formatted = d_str
                    grouped[d_str].append({
                        **e,
                        "day_name": day_name,
                        "day_formatted": day_formatted,
                    })
                else:
                    grouped["unscheduled"].append({
                        **e,
                        "day_name": "Other",
                        "day_formatted": "Other",
                    })

            return JSONResponse({
                "events": events,
                "saved_calendars": saved_cals,
                "grouped": dict(grouped)
            })
        except Exception as e:
            logger.error(f"Error handling calendar request: {e}")
            return JSONResponse({"events": [], "saved_calendars": [], "grouped": {}, "error": str(e)})

    async def _handle_calendar_delete(self, request):
        """Deletes a saved calendar document and optionally clears all schedule events."""
        try:
            body = await request.json()
            doc_id = int(body.get("doc_id", 0))
            clear_events = bool(body.get("clear_events", False))
            if not doc_id:
                return JSONResponse({"error": "Missing doc_id"}, status_code=400)

            mem = None
            if self.orchestrator and hasattr(self.orchestrator, "memory"):
                mem = self.orchestrator.memory
            else:
                from jarvis.skills.calendar_manager import _get_memory
                mem = _get_memory()

            doc_title = f"Calendar #{doc_id}"
            if mem:
                with mem._get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT title FROM documents WHERE id = ?", (doc_id,))
                    row = cur.fetchone()
                    if row:
                        doc_title = row["title"]

            success = mem.delete_saved_calendar(doc_id) if mem else False
            events_cleared = 0
            if clear_events and mem:
                events_cleared = mem.clear_calendar_events()

            self.broadcast({
                "type": "calendar_deleted",
                "doc_id": doc_id,
                "title": doc_title,
                "events_cleared": events_cleared,
                "message": f"Calendar '{doc_title}' deleted successfully, Sir."
            })

            return JSONResponse({
                "status": "deleted" if success else "not_found",
                "doc_id": doc_id,
                "title": doc_title,
                "events_cleared": events_cleared,
                "message": f"Calendar '{doc_title}' has been deleted."
            })
        except Exception as e:
            logger.error(f"Error deleting calendar: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _handle_calendar_event_delete(self, request):
        """Deletes an individual calendar event by numerical ID."""
        try:
            body = await request.json()
            event_id = int(body.get("event_id", 0))
            if not event_id:
                return JSONResponse({"error": "Missing event_id"}, status_code=400)

            mem = None
            if self.orchestrator and hasattr(self.orchestrator, "memory"):
                mem = self.orchestrator.memory
            else:
                from jarvis.skills.calendar_manager import _get_memory
                mem = _get_memory()

            success = mem.delete_calendar_event(event_id) if mem else False

            self.broadcast({
                "type": "calendar_event_deleted",
                "event_id": event_id,
                "message": f"Calendar event #{event_id} deleted."
            })

            return JSONResponse({
                "status": "deleted" if success else "not_found",
                "event_id": event_id
            })
        except Exception as e:
            logger.error(f"Error deleting calendar event: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _handle_calendar_clear(self, request):
        """Purges all active calendar events from memory."""
        try:
            mem = None
            if self.orchestrator and hasattr(self.orchestrator, "memory"):
                mem = self.orchestrator.memory
            else:
                from jarvis.skills.calendar_manager import _get_memory
                mem = _get_memory()

            count = mem.clear_calendar_events() if mem else 0

            self.broadcast({
                "type": "calendar_cleared",
                "count": count,
                "message": f"Active schedule cleared ({count} events purged), Sir."
            })

            return JSONResponse({
                "status": "cleared",
                "count": count,
                "message": f"Cleared {count} calendar events."
            })
        except Exception as e:
            logger.error(f"Error clearing calendar events: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _handle_websocket(self, websocket: WebSocket):
        accepted = await self.telemetry_manager.connect(websocket)
        if not accepted:
            return

        # Send initial status asynchronously off-loop
        try:
            init_telemetry = await anyio.to_thread.run_sync(
                WorkstationMetricsCollector.sample_metrics_sync, self.orchestrator
            )
            await websocket.send_text(json.dumps({"type": "telemetry", **init_telemetry}, separators=(",", ":")))
        except Exception:
            pass

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    payload = json.loads(data_text)
                    msg_type = payload.get("type", "")

                    if msg_type == "command":
                        cmd = payload.get("text", "").strip()
                        if cmd and self.orchestrator:
                            threading.Thread(target=self.orchestrator.handle_command, args=(cmd,), daemon=True).start()

                    elif msg_type == "action":
                        action = payload.get("action", "")
                        if action == "toggle_mute" and self.orchestrator:
                            is_muted = self.orchestrator.toggle_mute()
                            self.broadcast_telemetry({"is_muted": is_muted, "m": 1 if is_muted else 0})
                        elif action == "set_volume":
                            from jarvis.skills.os_control import adjust_volume
                            percent = int(payload.get("percent", 50))
                            adjust_volume(action="set", percent=percent)
                            self.broadcast_message("jarvis", f"Master volume set to {percent}%, Sir.")

                except Exception as e:
                    logger.warning(f"Error handling HUD WebSocket message: {e}")

        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            await self.telemetry_manager.disconnect(websocket)

    def _gather_telemetry(self) -> Dict[str, Any]:
        """Collects current hardware and model telemetry without blocking."""
        return WorkstationMetricsCollector.sample_metrics_sync(self.orchestrator)

    def broadcast(self, payload: Dict[str, Any]):
        """Thread-safe broadcast of a JSON payload to all connected clients."""
        if not self.telemetry_manager.client_count or not self._loop:
            return

        message = json.dumps(payload, separators=(",", ":"))
        try:
            asyncio.run_coroutine_threadsafe(self.telemetry_manager.broadcast_direct(message), self._loop)
        except Exception:
            pass

    def broadcast_state(self, state: str):
        """Broadcasts voice state change ('idle', 'listening', 'thinking', 'speaking')."""
        self.broadcast({"type": "state", "state": state, "st": state})

    def broadcast_message(self, sender: str, text: str):
        """Broadcasts a dialogue message to the live mission feed."""
        self.broadcast({
            "type": "chat",
            "sender": sender,
            "text": text,
            "timestamp": time.strftime("%H:%M:%S"),
        })

    def broadcast_telemetry(self, extra: Optional[Dict[str, Any]] = None):
        """Broadcasts telemetry update."""
        base = self._gather_telemetry()
        if extra:
            base.update(extra)
        if self._loop and self.telemetry_manager.client_count:
            try:
                self._loop.call_soon_threadsafe(self.telemetry_manager.broadcast_telemetry, base)
            except Exception:
                pass

    def broadcast_audio_level(self, level: float):
        """Broadcasts audio reactivity level (0.0 to 1.0)."""
        self.broadcast({"type": "audio_level", "level": max(0.0, min(1.0, level))})

    def start(self):
        """Starts the Uvicorn server in a background daemon thread with continuous live telemetry streaming."""
        if self._thread and self._thread.is_alive():
            return

        async def _telemetry_broadcast_loop():
            # Prime CPU counter baseline
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

            while self._server and not self._server.should_exit:
                try:
                    await asyncio.sleep(1.0)
                    if self.telemetry_manager.client_count:
                        # Offload synchronous system queries to worker thread: ZERO event-loop blocking
                        metrics = await anyio.to_thread.run_sync(
                            WorkstationMetricsCollector.sample_metrics_sync, self.orchestrator
                        )
                        self.telemetry_manager.broadcast_telemetry(metrics)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Telemetry broadcast loop notice: {e}")

        async def _serve_all():
            telemetry_task = self._loop.create_task(_telemetry_broadcast_loop())
            try:
                await self._server.serve()
            finally:
                telemetry_task.cancel()
                try:
                    await telemetry_task
                except asyncio.CancelledError:
                    pass

        def _run_server():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            config = uvicorn.Config(
                app=self._create_app(),
                host=self.host,
                port=self.port,
                log_level="warning",
                loop="asyncio",
            )
            self._server = uvicorn.Server(config)
            logger.info(f"Starting Jarvis HUD Web Server at http://{self.host}:{self.port}...")
            self._loop.run_until_complete(_serve_all())

        self._thread = threading.Thread(target=_run_server, daemon=True, name="Jarvis-HUD-Server")
        self._thread.start()
        time.sleep(0.4)

    def stop(self):
        """Stops the Uvicorn server cleanly and shuts down native NVML session."""
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._server = None
        WorkstationMetricsCollector.shutdown_nvml()
