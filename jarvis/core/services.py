"""Lifecycle management for local dependency processes used by Jarvis."""
import logging
import subprocess
import threading
from typing import Any, Dict, Tuple

logger = logging.getLogger("Jarvis.Services")


class _PsutilProcessHandle:
    """Small Popen-compatible wrapper for a verified local service process."""

    def __init__(self, process: Any):
        self._process = process
        self.pid = process.pid

    def poll(self):
        try:
            return None if self._process.is_running() else 0
        except Exception:
            return 0

    def terminate(self):
        self._process.terminate()

    def wait(self, timeout=None):
        return self._process.wait(timeout=timeout)

    def kill(self):
        self._process.kill()


class ManagedServiceManager:
    """Tracks spawned services and verified local listeners for shutdown cleanup."""

    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def register(self, name: str, process: Any) -> None:
        """Register a spawned service process for shutdown cleanup."""
        with self._lock:
            self._services[name] = process
        logger.info("Registered managed service %s (PID %s).", name, process.pid)

    def adopt_local_listener(self, name: str, port: int, identity_tokens: Tuple[str, ...]) -> bool:
        """Adopt a local listener only when its process identity matches the expected service."""
        try:
            import psutil
        except ImportError:
            logger.warning("Cannot identify existing %s on port %s because psutil is unavailable.", name, port)
            return False

        try:
            for connection in psutil.net_connections(kind="tcp"):
                if connection.status != psutil.CONN_LISTEN or connection.laddr.port != port or not connection.pid:
                    continue
                try:
                    process = psutil.Process(connection.pid)
                    identity = " ".join([process.name(), *process.cmdline()]).lower()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                if all(token.lower() in identity for token in identity_tokens):
                    self.register(name, _PsutilProcessHandle(process))
                    logger.info("Adopted local %s listener on port %s for shutdown cleanup.", name, port)
                    return True
        except Exception:
            logger.exception("Could not inspect local listeners while adopting %s.", name)
        logger.warning("No verifiable local %s process was found listening on port %s.", name, port)
        return False

    def stop_all(self, timeout_seconds: float = 5.0) -> None:
        """Stop registered services and their child processes, then force-stop stragglers."""
        with self._lock:
            services: Tuple[Tuple[str, Any], ...] = tuple(self._services.items())
            self._services.clear()

        for name, process in services:
            if process.poll() is not None:
                continue
            descendants = []
            try:
                import psutil
                descendants = psutil.Process(process.pid).children(recursive=True)
            except Exception:
                # The primary child can still be stopped without the optional psutil package.
                descendants = []
            try:
                logger.info("Stopping managed service %s (PID %s).", name, process.pid)
                for child in reversed(descendants):
                    try:
                        child.terminate()
                    except Exception:
                        pass
                process.terminate()
                try:
                    process.wait(timeout=timeout_seconds)
                except Exception as wait_error:
                    timeout_errors = [subprocess.TimeoutExpired]
                    try:
                        import psutil
                        timeout_errors.append(psutil.TimeoutExpired)
                    except ImportError:
                        pass
                    if not isinstance(wait_error, tuple(timeout_errors)):
                        raise
                    logger.warning("Managed service %s did not stop in time; terminating it.", name)
                    process.kill()
                    process.wait(timeout=2.0)
                if descendants:
                    import psutil
                    _, still_running = psutil.wait_procs(descendants, timeout=timeout_seconds)
                    for child in still_running:
                        try:
                            child.kill()
                        except Exception:
                            pass
                    if still_running:
                        psutil.wait_procs(still_running, timeout=2.0)
            except Exception:
                logger.exception("Could not stop managed service %s (PID %s).", name, process.pid)
