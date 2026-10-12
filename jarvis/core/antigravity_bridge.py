"""Antigravity CLI (agy) Bridge for Jarvis 2.0.
Leverages authenticated Google Antigravity CLI sessions to tap into Google AI Pro
(Gemini 3.1 Pro, Gemini 3.8 Flash, Claude Sonnet 4.6, Claude Opus 4.6) with zero API keys.
"""
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("Jarvis.Antigravity")


class AntigravityBridge:
    """Bridges Jarvis to local Google Antigravity CLI (`agy`) daemon/executable.
    Enables zero-cost access to Gemini 3.1 Pro, Gemini 3.8 Flash, Claude Sonnet 4.6,
    and Claude Opus using the user's active Google account subscription.
    """

    # Comprehensive catalog of Antigravity models and convenient shorthand aliases
    MODEL_CATALOG: Dict[str, str] = {
        # Gemini 3.8 Flash Tier (3 variants: low, medium, high)
        "gemini-3.8-flash": "gemini-3.8-flash-low",
        "gemini-3.8-flash-low": "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high": "gemini-3.8-flash-high",
        "flash": "gemini-3.8-flash-low",
        "flash-low": "gemini-3.8-flash-low",
        "flash-medium": "gemini-3.8-flash-medium",
        "flash-high": "gemini-3.8-flash-high",
        "3.8": "gemini-3.8-flash-low",
        "3.8-low": "gemini-3.8-flash-low",
        "3.8-medium": "gemini-3.8-flash-medium",
        "3.8-high": "gemini-3.8-flash-high",
        "gemini-flash": "gemini-3.8-flash-low",

        # Gemini 3.1 Pro Tier (2 variants: low, high)
        "gemini-3.1-pro": "gemini-3.1-pro-low",
        "gemini-3.1-pro-low": "gemini-3.1-pro-low",
        "gemini-3.1-pro-high": "gemini-3.1-pro-high",
        "pro": "gemini-3.1-pro-low",
        "pro-low": "gemini-3.1-pro-low",
        "pro-high": "gemini-3.1-pro-high",
        "3.1": "gemini-3.1-pro-low",
        "3.1-low": "gemini-3.1-pro-low",
        "3.1-high": "gemini-3.1-pro-high",
        "gemini-pro": "gemini-3.1-pro-low",

        # Claude Frontier Tier (Available in Antigravity)
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-sonnet": "claude-sonnet-4-6",
        "sonnet": "claude-sonnet-4-6",
        "claude": "claude-sonnet-4-6",
        "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
        "claude-opus": "claude-opus-4-6-thinking",
        "opus": "claude-opus-4-6-thinking",

        # Open-Weights Frontier Tier
        "gpt-oss-120b-medium": "gpt-oss-120b-medium",
        "gpt-oss": "gpt-oss-120b-medium",
    }

    TIER_FLASH = [
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high",
    ]
    TIER_PRO = [
        "gemini-3.1-pro-low",
        "gemini-3.1-pro-high",
    ]
    TIER_CLAUDE = [
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
    ]

    DEFAULT_MODEL = "gemini-3.1-pro-low"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})
        self.agy_path = self._find_agy_executable()
        self.configured_model = llm_cfg.get("antigravity_model", self.DEFAULT_MODEL)
        self.effort = llm_cfg.get("antigravity_effort", "low")
        self.last_latency_ms: float = 0.0
        self.last_conversation_id: Optional[str] = None

        if self.agy_path:
            logger.info("Antigravity CLI bridge located at: %s", self.agy_path)
        else:
            logger.warning("Antigravity CLI ('agy') executable not found in PATH or standard directories.")

    def _find_agy_executable(self) -> Optional[str]:
        """Locates the agy CLI binary across PATH and standard installation paths."""
        # 1. System PATH
        found = shutil.which("agy") or shutil.which("agy.exe")
        if found:
            return str(Path(found).resolve())

        # 2. Local AppData / agy / bin
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidate = Path(local_app_data) / "agy" / "bin" / "agy.exe"
            if candidate.exists():
                return str(candidate.resolve())

        # 3. User home .gemini / antigravity-cli / bin
        home = Path.home()
        candidate2 = home / ".gemini" / "antigravity-cli" / "bin" / "agy.exe"
        if candidate2.exists():
            return str(candidate2.resolve())

        return None

    def is_available(self) -> bool:
        """Returns True if the Antigravity CLI binary exists and can be executed."""
        return self.agy_path is not None and os.path.exists(self.agy_path)

    def resolve_model(self, model_name: str) -> str:
        """Resolves alias or shorthand into canonical Antigravity model identifier."""
        clean = (model_name or "").lower().strip()
        if clean in self.MODEL_CATALOG:
            return self.MODEL_CATALOG[clean]
        # Direct pass-through if exact match
        return model_name.strip() if model_name else self.DEFAULT_MODEL

    def list_models(self) -> List[str]:
        """Returns unique list of canonical models supported by the bridge."""
        seen = set()
        models = []
        for v in self.MODEL_CATALOG.values():
            if v not in seen:
                seen.add(v)
                models.append(v)
        return models

    def get_candidate_cascade(self, model: str) -> List[str]:
        """Builds a priority fallback cascade within the Google AI Pro fleet."""
        resolved = self.resolve_model(model)
        if "flash" in resolved:
            pool = list(self.TIER_FLASH)
        elif "pro" in resolved:
            pool = list(self.TIER_PRO) + ["gemini-3.8-flash-low"]
        elif "claude" in resolved:
            pool = list(self.TIER_CLAUDE) + ["gemini-3.1-pro-low", "gemini-3.8-flash-low"]
        else:
            pool = [resolved, "gemini-3.8-flash-low", "gemini-3.1-pro-low"]

        # Ensure requested model is first
        ordered = [resolved] + [m for m in pool if m != resolved]
        return ordered

    def classify_prompt_tier(self, prompt: str) -> str:
        """Determines optimal Google AI Pro model tier based on task complexity."""
        p = prompt.lower().strip()
        # 1. Code & Technical Engineering -> Claude Sonnet 4.6
        code_words = ["def ", "class ", "function", "refactor", "bug", "traceback", "git ", "compile", "script", "syntax", "patch", "pytest", "unittest", "repo", "import "]
        if any(w in p for w in code_words):
            return "claude-sonnet-4-6"

        # 2. Hard Reasoning, Math, Logic, Planning -> Gemini 3.1 Pro
        reasoning_words = ["analyze", "plan", "strategy", "why does", "math", "calculate", "solve", "syllabus", "timetable", "essay", "architecture", "compare", "proof"]
        if any(w in p for w in reasoning_words) or len(p.split()) > 25:
            return "gemini-3.1-pro-low"

        # 3. Fast Voice, Banter, Greetings, Device control -> Gemini 3.8 Flash
        return "gemini-3.8-flash-low"

    def generate_response(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: str = "",
        sentence_callback: Optional[Callable[[str], None]] = None,
        timeout: float = 90.0,
        conversation_id: Optional[str] = None,
        candidates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generates an LLM response using the authenticated Antigravity CLI.
        Supports automatic cascading pool failover within the Google AI Pro fleet.
        """
        if not self.is_available():
            return {
                "status": "ERROR",
                "response": "Antigravity CLI ('agy') is not installed or available in PATH, Sir.",
                "duration_seconds": 0.0,
                "usage": {},
            }

        target_model = self.resolve_model(model or self.configured_model)
        full_prompt = prompt.strip()
        if system_instruction and system_instruction.strip():
            full_prompt = f"[System Context & Directives]\n{system_instruction.strip()}\n\n[User Request]\n{full_prompt}"

        # Cascade through candidate models in priority order
        cascade_models = candidates or self.get_candidate_cascade(target_model)
        last_error_res = None

        for attempt_model in cascade_models:
            if sentence_callback:
                res = self._generate_streaming(
                    prompt=full_prompt,
                    model=attempt_model,
                    sentence_callback=sentence_callback,
                    timeout=timeout,
                    conversation_id=conversation_id,
                )
            else:
                res = self._generate_batch(
                    prompt=full_prompt,
                    model=attempt_model,
                    timeout=timeout,
                    conversation_id=conversation_id,
                )

            if res.get("status") == "SUCCESS":
                return res

            last_error_res = res
            logger.warning(
                "Antigravity model candidate '%s' failed (%s). Cascading to next candidate in pool...",
                attempt_model,
                res.get("response", "")[:80]
            )

        return last_error_res or {
            "status": "ERROR",
            "response": "All Antigravity candidate models failed, Sir.",
            "duration_seconds": 0.0,
            "usage": {},
        }

    def _generate_batch(
        self,
        prompt: str,
        model: str,
        timeout: float = 90.0,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a single-turn query via --output-format json."""
        cmd = [
            self.agy_path,
            "--model", model,
            "--disable-slash-commands",
            "--output-format", "json",
        ]
        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        t_start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                encoding="utf-8",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            elapsed = time.perf_counter() - t_start
            self.last_latency_ms = elapsed * 1000.0

            if proc.returncode != 0:
                err_msg = proc.stderr.strip() or f"CLI exited with code {proc.returncode}"
                logger.error("Antigravity batch invocation error: %s", err_msg)
                return {
                    "status": "ERROR",
                    "response": f"Antigravity CLI error: {err_msg}",
                    "duration_seconds": elapsed,
                    "usage": {},
                }

            stdout_str = proc.stdout.strip()
            if not stdout_str:
                return {
                    "status": "ERROR",
                    "response": "Antigravity CLI returned an empty response.",
                    "duration_seconds": elapsed,
                    "usage": {},
                }

            data = json.loads(stdout_str)
            resp_text = (data.get("response") or "").strip()
            self.last_conversation_id = data.get("conversation_id")
            return {
                "status": data.get("status", "SUCCESS"),
                "response": resp_text,
                "duration_seconds": float(data.get("duration_seconds", elapsed)),
                "usage": data.get("usage", {}),
                "conversation_id": self.last_conversation_id,
                "model": model,
            }

        except subprocess.TimeoutExpired:
            logger.error("Antigravity batch execution timed out after %.1fs", timeout)
            return {
                "status": "TIMEOUT",
                "response": f"Antigravity CLI request timed out after {timeout} seconds, Sir.",
                "duration_seconds": timeout,
                "usage": {},
            }
        except Exception as e:
            logger.error("Antigravity execution failed: %s", e)
            return {
                "status": "ERROR",
                "response": f"Antigravity execution error: {e}",
                "duration_seconds": time.perf_counter() - t_start,
                "usage": {},
            }

    def _generate_streaming(
        self,
        prompt: str,
        model: str,
        sentence_callback: Callable[[str], None],
        timeout: float = 90.0,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes query with --output-format stream-json, extracting text deltas
        and firing sentence_callback upon clause/sentence completions.
        """
        cmd = [
            self.agy_path,
            "--model", model,
            "--disable-slash-commands",
            "--output-format", "stream-json",
        ]
        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        t_start = time.perf_counter()
        accumulated_text = []
        speech_buffer = ""
        final_result_data: Dict[str, Any] = {}

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # Send prompt and close stdin to signal EOF
            proc.stdin.write(prompt)
            proc.stdin.close()

            # Read NDJSON lines in real time
            for line in iter(proc.stdout.readline, ""):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    event_data = json.loads(line_str)
                    event_type = event_data.get("event")

                    if event_type == "step_update":
                        step_up = event_data.get("step_update", {})
                        delta = step_up.get("text_delta", "")
                        if delta:
                            accumulated_text.append(delta)
                            speech_buffer += delta

                            # Check for clause/sentence boundary to stream to TTS
                            chunk, remaining = self._extract_speech_chunk(speech_buffer)
                            if chunk:
                                speech_buffer = remaining
                                sentence_callback(chunk)

                    elif event_type == "result":
                        final_result_data = event_data.get("result", {})
                        if final_result_data.get("conversation_id"):
                            self.last_conversation_id = final_result_data.get("conversation_id")

                except Exception as parse_err:
                    logger.debug("Non-critical stream JSON parse error on line: %s (%s)", line_str[:60], parse_err)

            proc.stdout.close()
            proc.wait(timeout=5.0)

            # Flush any remaining text in speech buffer to TTS
            if speech_buffer.strip():
                clean_tail = self._clean_speech_clause(speech_buffer)
                if clean_tail:
                    sentence_callback(clean_tail)

            elapsed = time.perf_counter() - t_start
            self.last_latency_ms = elapsed * 1000.0

            full_response = final_result_data.get("response") or "".join(accumulated_text).strip()
            return {
                "status": final_result_data.get("status", "SUCCESS"),
                "response": full_response,
                "duration_seconds": float(final_result_data.get("duration_seconds", elapsed)),
                "usage": final_result_data.get("usage", {}),
                "conversation_id": self.last_conversation_id,
                "model": model,
            }

        except Exception as e:
            logger.error("Antigravity streaming failed: %s", e)
            elapsed = time.perf_counter() - t_start
            return {
                "status": "ERROR",
                "response": "".join(accumulated_text).strip() or f"Antigravity streaming error: {e}",
                "duration_seconds": elapsed,
                "usage": {},
            }
        finally:
            if proc:
                try:
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe and not getattr(pipe, "closed", True):
                            pipe.close()
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    def _extract_speech_chunk(buffer: str) -> Tuple[Optional[str], str]:
        """Extracts the next complete spoken sentence or natural clause from buffer."""
        # 1. Prefer sentence terminal punctuation (. ! ? \n)
        m = re.search(r'(?<!\d)(?<!\b[A-Za-z])[.!?\n]+(?!\d)(\s+|$)', buffer)
        if m and m.start() >= 2:
            idx = m.end()
            return buffer[:idx].strip(), buffer[idx:]
        # 2. Natural clause punctuation if buffer exceeds 60 characters
        if len(buffer) > 60:
            m_clause = re.search(r'(?:[,;]|(?<!\d):(?!\d))\s+', buffer[20:])
            if m_clause:
                cut_idx = 20 + m_clause.end()
                return buffer[:cut_idx].strip(), buffer[cut_idx:]
        return None, buffer

    @staticmethod
    def _clean_speech_clause(text: str) -> str:
        """Sanitizes raw markdown tokens and normalizes speech clauses."""
        if not text:
            return ""
        from jarvis.audio.normalizer import SpeechNormalizer
        cleaned = re.sub(r'[*_#`~>\[\]\(\)\{\}\\|]', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return SpeechNormalizer.normalize(cleaned)
