"""Starlette and WebSocket server powering the Jarvis 2.0 Holographic Web HUD."""
import asyncio
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional, Set
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn
import psutil

from jarvis.skills.os_control import get_raw_battery_info

logger = logging.getLogger("Jarvis.UI")

_gpu_telemetry_cache = {"data": None, "timestamp": 0.0}


def _get_cached_gpu_telemetry() -> Optional[Dict[str, Any]]:
    """Caches GPU telemetry for 6 seconds to avoid spawning nvidia-smi constantly."""
    now = time.time()
    if now - _gpu_telemetry_cache["timestamp"] < 6.0 and _gpu_telemetry_cache["data"] is not None:
        return _gpu_telemetry_cache["data"]
    try:
        from jarvis.skills.system_watchdog import _query_gpu_telemetry
        res = _query_gpu_telemetry()
        if res:
            _gpu_telemetry_cache["data"] = res
            _gpu_telemetry_cache["timestamp"] = now
            return res
    except Exception:
        pass
    return _gpu_telemetry_cache.get("data")


class JarvisUIService:
    """Manages the lifecycle of the Starlette HUD server and WebSocket broadcasting."""

    _instance: Optional["JarvisUIService"] = None

    def __init__(self, orchestrator: Any = None, host: str = "127.0.0.1", port: int = 20129):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.connected_clients: Set[WebSocket] = set()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.template_path = Path(__file__).parent / "templates" / "hud.html"
        JarvisUIService._instance = self
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

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
            WebSocketRoute("/ws", endpoint=self._handle_websocket),
        ]
        return Starlette(routes=routes)

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

    async def _handle_websocket(self, websocket: WebSocket):
        await websocket.accept()
        self.connected_clients.add(websocket)
        logger.info(f"HUD WebSocket client connected (Total: {len(self.connected_clients)})")

        # Send initial status
        try:
            init_telemetry = self._gather_telemetry()
            await websocket.send_text(json.dumps({"type": "telemetry", **init_telemetry}))
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
                            self.broadcast_telemetry({"is_muted": is_muted})
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
            self.connected_clients.discard(websocket)
            logger.info(f"HUD WebSocket client disconnected (Total: {len(self.connected_clients)})")

    def _gather_telemetry(self) -> Dict[str, Any]:
        """Collects current hardware and model telemetry without blocking."""
        bat = get_raw_battery_info()
        cpu_val = round(psutil.cpu_percent(interval=None), 1)
        mem = psutil.virtual_memory()
        ram_pct = mem.percent
        ram_used_gb = round(mem.used / (1024 ** 3), 1)
        ram_total_gb = round(mem.total / (1024 ** 3), 1)

        gpu_info = _get_cached_gpu_telemetry()
        gpu_temp = gpu_info.get("temp_c", 0) if gpu_info else 0
        gpu_name = gpu_info.get("name", "RTX 3050 (CUDA FP16)") if gpu_info else "RTX 3050 (CUDA FP16)"
        gpu_vram = f"{gpu_info.get('vram_used', 0)} MB" if gpu_info else "Active"

        timer_count = 0
        try:
            from jarvis.skills.timers import _active_timers, _timer_lock
            with _timer_lock:
                timer_count = len(_active_timers)
        except Exception:
            pass

        active_model = "qwen/qwen3.8-27b + qwen3.5:2b"
        tier = "TRI-TIER HYBRID"
        provider = "hybrid"
        specialist_status = {"gemini": "ACTIVE", "groq": "ACTIVE", "grok": "METERED / OFF", "claude": "METERED / OFF"}
        is_muted = False

        if self.orchestrator:
            is_muted = self.orchestrator.is_muted()
            if hasattr(self.orchestrator, "brain"):
                p = getattr(self.orchestrator.brain, "provider", "hybrid")
                provider = p
                tier = "TRI-TIER HYBRID" if p in ("hybrid", "auto") else ("LOCAL" if p == "ollama" else "CLOUD")
                active_model = getattr(self.orchestrator.brain, "openai_model", active_model)
                status_provider = getattr(self.orchestrator.brain, "specialist_status", None)
                if callable(status_provider):
                    specialist_status = status_provider()

        latencies = {"ollama": 0.0, "groq": 0.0, "gemini": 0.0}
        if self.orchestrator and hasattr(self.orchestrator, "brain"):
            lat_getter = getattr(self.orchestrator.brain, "get_latencies", None)
            if callable(lat_getter):
                latencies = lat_getter()

        memory_label = "TIER 2 (ACTIVE)"
        if self.orchestrator and hasattr(self.orchestrator, "brain") and hasattr(self.orchestrator.brain, "memory"):
            try:
                facts = self.orchestrator.brain.memory.get_all_facts()
                memory_label = f"{len(facts)} FACTS STORED"
            except Exception:
                pass

        return {
            "cpu": cpu_val,
            "ram_pct": ram_pct,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "battery": bat.get("percent", 100),
            "charging": bat.get("charging", False),
            "has_battery": bat.get("has_battery", False),
            "gpu": gpu_name,
            "gpu_temp": gpu_temp,
            "gpu_vram": gpu_vram,
            "model": active_model,
            "tier": tier,
            "provider": provider,
            "specialists": specialist_status,
            "active_timers": timer_count,
            "latencies": latencies,
            "memory_label": memory_label,
            "is_muted": is_muted,
            "timestamp": time.strftime("%H:%M:%S"),
        }

    def broadcast(self, payload: Dict[str, Any]):
        """Thread-safe broadcast of a JSON payload to all connected clients."""
        if not self.connected_clients or not self._loop:
            return

        message = json.dumps(payload)

        async def _send():
            dead = set()
            for client in list(self.connected_clients):
                try:
                    await client.send_text(message)
                except Exception:
                    dead.add(client)
            for d in dead:
                self.connected_clients.discard(d)

        try:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)
        except Exception:
            pass

    def broadcast_state(self, state: str):
        """Broadcasts voice state change ('idle', 'listening', 'thinking', 'speaking')."""
        self.broadcast({"type": "state", "state": state})

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
        self.broadcast({"type": "telemetry", **base})

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
                    if self.connected_clients:
                        base = self._gather_telemetry()
                        msg = json.dumps({"type": "telemetry", **base})
                        dead = set()
                        for client in list(self.connected_clients):
                            try:
                                await client.send_text(msg)
                            except Exception:
                                dead.add(client)
                        for d in dead:
                            self.connected_clients.discard(d)
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
        """Stops the Uvicorn server cleanly."""
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._server = None
