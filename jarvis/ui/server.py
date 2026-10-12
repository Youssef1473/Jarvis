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

from jarvis.skills.os_control import get_raw_battery_info

logger = logging.getLogger("Jarvis.UI")


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
        active_model = "qwen3.5:2b-q4_K_M"
        tier = "local"
        provider = "ollama"
        specialist_status = {"gemini": "ADD KEY", "groq": "ADD KEY", "grok": "METERED / OFF", "claude": "METERED / OFF"}
        is_muted = False

        if self.orchestrator:
            is_muted = self.orchestrator.is_muted()
            if hasattr(self.orchestrator, "brain"):
                active_model = getattr(self.orchestrator.brain, "openai_model", active_model)
                provider = getattr(self.orchestrator.brain, "provider", "")
                tier = "LOCAL" if provider == "ollama" else "CLOUD"
                status_provider = getattr(self.orchestrator.brain, "specialist_status", None)
                if callable(status_provider):
                    specialist_status = status_provider()

        return {
            "battery": bat.get("percent", 100),
            "charging": bat.get("charging", False),
            "gpu": "RTX 3050 (CUDA FP16)",
            "model": active_model,
            "tier": tier,
            "provider": provider,
            "specialists": specialist_status,
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
            for client in self.connected_clients:
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
        """Starts the Uvicorn server in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return

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
            self._loop.run_until_complete(self._server.serve())

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
