"""Antigravity CLI (agy) Bridge for Jarvis 2.0.
Leverages authenticated Google Antigravity CLI sessions to tap into Google AI Pro
(Gemini 3.8 Flash High/Medium/Low, Claude Sonnet 4.6, Claude Opus 4.6, and staged Gemini 4 Argon) with zero API keys.
"""
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("Jarvis.Antigravity")


class AntigravityDaemon:
    """Persistent warm agy.exe sidecar worker utilizing --input-format stream-json.
    Eliminates 3.1s process creation and workspace indexing overhead on every turn,
    dropping conversational turn latency from ~7,100ms down to sub-1,800ms.
    """

    def __init__(self, agy_path: str, default_model: str, default_effort: Optional[str] = None):
        self.agy_path = agy_path
        self.default_model = default_model
        self.default_effort = default_effort
        self.current_model: Optional[str] = None
        self.current_effort: Optional[str] = None
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.conversation_id: Optional[str] = None
        self.runtime_workspace = Path("data/agy_runtime_workspace").resolve()
        self.runtime_workspace.mkdir(parents=True, exist_ok=True)

    def is_alive(self) -> bool:
        """Returns True if the daemon child process is currently alive and responsive."""
        return self.proc is not None and self.proc.poll() is None

    def start(self, model: str, effort: Optional[str] = None) -> bool:
        """Spawns persistent worker process with --input-format stream-json."""
        self.stop()
        if not self.agy_path or not os.path.exists(self.agy_path):
            return False

        cmd = [
            self.agy_path,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", model,
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
        ]
        if effort:
            cmd.extend(["--effort", effort])

        try:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                cwd=str(self.runtime_workspace),
                creationflags=creation_flags,
            )
            self.current_model = model
            self.current_effort = effort

            # Wait for the initial init event
            init_deadline = time.time() + 15.0
            while time.time() < init_deadline:
                if self.proc.poll() is not None:
                    break
                line = self.proc.stdout.readline()
                if not line:
                    break
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if data.get("event") == "init":
                        self.conversation_id = data.get("conversation_id")
                        logger.info(
                            "Antigravity persistent warm daemon online (model: %s, cid: %s)",
                            model, self.conversation_id
                        )
                        return True
                except Exception:
                    pass

            logger.warning("Antigravity persistent daemon failed to initialize within deadline.")
            self.stop()
            return False
        except Exception as e:
            logger.error("Failed to spawn Antigravity persistent daemon: %s", e)
            self.stop()
            return False

    def stop(self):
        """Cleanly terminates the daemon process with a strict 2.0s deadline."""
        if getattr(self, "proc", None):
            try:
                for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    if pipe and not getattr(pipe, "closed", True):
                        try:
                            pipe.close()
                        except Exception:
                            pass
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=1.0)
            except Exception as e:
                logger.debug("Error while stopping Antigravity daemon: %s", e)
            finally:
                self.proc = None
                self.current_model = None
                self.current_effort = None

    def __del__(self):
        self.stop()

    def execute_turn(
        self,
        prompt: str,
        model: str,
        effort: Optional[str] = None,
        sentence_callback: Optional[Callable[[str], None]] = None,
        timeout: float = 90.0,
    ) -> Optional[Dict[str, Any]]:
        """Executes query turn against the persistent worker. Returns response dict or None on failure."""
        with self.lock:
            # Re-spawn if dead or configuration changed
            if not self.is_alive() or self.current_model != model or self.current_effort != effort:
                ok = self.start(model, effort)
                if not ok or not self.proc or not self.proc.stdin:
                    return None

            t_start = time.perf_counter()
            accumulated_text: List[str] = []
            speech_buffer = ""
            final_result_data: Dict[str, Any] = {}

            # Wrap in stream-json user message schema
            msg = {
                "event": "user",
                "message": {
                    "role": "user",
                    "content": prompt
                }
            }

            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                logger.warning("Antigravity daemon pipe broken on write: %s", e)
                self.stop()
                return None

            timed_out = False
            timeout_reason = ""
            stop_watchdog = threading.Event()
            # Sir directive: zero time restrictions on LLM reasoning and tasks. Run to natural completion.

            try:
                for line in iter(self.proc.stdout.readline, ""):
                    if timed_out:
                        break
                    last_activity_time = time.perf_counter()
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                        ev = data.get("event")

                        if ev == "step_update":
                            step_up = data.get("step_update", {})
                            delta = step_up.get("text_delta", "")
                            if delta:
                                accumulated_text.append(delta)
                                speech_buffer += delta

                                if sentence_callback:
                                    chunk, remaining = AntigravityBridge._extract_speech_chunk(speech_buffer)
                                    if chunk:
                                        speech_buffer = remaining
                                        sentence_callback(chunk)

                        elif ev == "result":
                            final_result_data = data.get("result", {})
                            if final_result_data.get("conversation_id"):
                                self.conversation_id = final_result_data.get("conversation_id")
                            break

                    except Exception as parse_err:
                        logger.debug("Daemon parse notice on stream line: %s (%s)", line_str[:60], parse_err)

            except (ValueError, OSError) as pipe_err:
                if not timed_out:
                    logger.warning("Antigravity daemon pipe error: %s", pipe_err)
                self.stop()
                return None
            except Exception as stream_err:
                logger.warning("Antigravity daemon streaming read error: %s", stream_err)
                self.stop()
                return None

            # Flush any remaining speech clause
            if sentence_callback and speech_buffer.strip():
                clean_tail = AntigravityBridge._clean_speech_clause(speech_buffer)
                if clean_tail:
                    sentence_callback(clean_tail)

            elapsed = time.perf_counter() - t_start
            err_msg = final_result_data.get("error") or ""
            full_response = final_result_data.get("response") or "".join(accumulated_text).strip()
            status = final_result_data.get("status", "SUCCESS" if (full_response and not err_msg) else "ERROR")

            if not full_response and not final_result_data:
                logger.warning("Antigravity daemon returned empty response.")
                return None

            return {
                "status": status,
                "response": full_response if full_response else err_msg,
                "error": err_msg,
                "duration_seconds": elapsed,
                "usage": final_result_data.get("usage", {}),
                "conversation_id": self.conversation_id,
                "model": model,
                "warm_daemon": True,
            }


class AntigravityBridge:
    """Bridges Jarvis to local Google Antigravity CLI (`agy`) daemon/executable.
    Enables zero-cost access to Gemini 3.8 Flash (High/Medium/Low), Claude Sonnet 4.6,
    Claude Opus 4.6, and staged Gemini 4 Argon using the user's active Google account subscription.
    """

    # Comprehensive catalog of Antigravity models and convenient shorthand aliases
    MODEL_CATALOG: Dict[str, str] = {
        # Gemini 3.8 Flash Tier (Primary Active Engine - High, Medium, Low Reasoning)
        "gemini-3.8-flash": "gemini-3.8-flash-high",
        "gemini-3.8-flash-high": "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low": "gemini-3.8-flash-low",
        "flash": "gemini-3.8-flash-low",
        "flash-low": "gemini-3.8-flash-low",
        "flash-medium": "gemini-3.8-flash-medium",
        "flash-high": "gemini-3.8-flash-high",
        "3.8": "gemini-3.8-flash-high",
        "3.8-low": "gemini-3.8-flash-low",
        "3.8-medium": "gemini-3.8-flash-medium",
        "3.8-high": "gemini-3.8-flash-high",
        "gemini-flash": "gemini-3.8-flash-low",

        # Legacy Pro Aliases -> Re-routed seamlessly to Gemini 3.8 Flash High
        "gemini-3.1-pro": "gemini-3.8-flash-high",
        "gemini-3.1-pro-low": "gemini-3.8-flash-high",
        "gemini-3.1-pro-high": "gemini-3.8-flash-high",
        "pro": "gemini-3.8-flash-high",
        "pro-low": "gemini-3.8-flash-high",
        "pro-high": "gemini-3.8-flash-high",
        "3.1": "gemini-3.8-flash-high",
        "3.1-low": "gemini-3.8-flash-high",
        "3.1-high": "gemini-3.8-flash-high",
        "gemini-pro": "gemini-3.8-flash-high",

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

        # Staged Gemini 4 Argon Tier (Forward-Compatibility Placeholders)
        "gemini-4-argon-high": "gemini-4-argon-high",
        "gemini-4-argon-medium": "gemini-4-argon-medium",
        "gemini-4-argon-low": "gemini-4-argon-low",
        "gemini-4-argon": "gemini-4-argon-high",
        "gemini-4": "gemini-4-argon-high",
        "argon": "gemini-4-argon-high",
        "argon-high": "gemini-4-argon-high",
        "argon-medium": "gemini-4-argon-medium",
        "argon-low": "gemini-4-argon-low",
    }

    TIER_FLASH = [
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-low",
    ]
    TIER_ARGON = [
        "gemini-4-argon-high",
        "gemini-4-argon-medium",
        "gemini-4-argon-low",
    ]
    TIER_CLAUDE = [
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
    ]

    DEFAULT_MODEL = "gemini-3.8-flash-medium"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})
        self.agy_path = self._find_agy_executable()
        self.configured_model = llm_cfg.get("antigravity_model", self.DEFAULT_MODEL)
        self.effort = llm_cfg.get("antigravity_effort", "high")
        self.last_latency_ms: float = 0.0
        self.last_conversation_id: Optional[str] = None
        self.use_daemon: bool = llm_cfg.get("antigravity_use_daemon", True)

        self.daemon: Optional[AntigravityDaemon] = None
        self._claude_exhausted_until: float = 0.0
        self._claude_exhaustion_reason: str = ""
        if self.agy_path and self.use_daemon:
            self.daemon = AntigravityDaemon(
                agy_path=self.agy_path,
                default_model=self.configured_model,
                default_effort=self.effort,
            )

        if self.agy_path:
            logger.info("Antigravity CLI bridge located at: %s (persistent daemon=%s)", self.agy_path, self.use_daemon)
        else:
            logger.warning("Antigravity CLI ('agy') executable not found in PATH or standard directories.")

    def is_claude_exhausted(self) -> bool:
        """Returns True if Claude Sonnet is currently in a token/quota exhaustion cooldown."""
        return time.time() < self._claude_exhausted_until

    def mark_claude_exhausted(self, duration_seconds: float = 900.0, reason: str = "") -> None:
        """Marks Claude Sonnet as token/quota-exhausted for a cooldown period (default 15 minutes)."""
        self._claude_exhausted_until = time.time() + duration_seconds
        self._claude_exhaustion_reason = reason
        logger.warning(
            "Claude Sonnet marked token/quota exhausted for %.0fs. Reason: %s",
            duration_seconds,
            reason[:120] if reason else "Rate limit / quota exceeded"
        )
        # If daemon was actively bound to Claude, terminate it cleanly so it can restart on Gemini Flash
        if self.daemon and self.daemon.current_model and "claude" in self.daemon.current_model:
            self.daemon.stop()

    def reset_claude_exhaustion(self) -> None:
        """Clears the Claude Sonnet token exhaustion status immediately."""
        self._claude_exhausted_until = 0.0
        self._claude_exhaustion_reason = ""
        logger.info("Claude Sonnet token exhaustion status manually cleared.")

    @staticmethod
    def _is_token_exhaustion_error(error_msg: str) -> bool:
        """Checks if an error string indicates token exhaustion, rate limit, quota, credit issues, or CLI code 3."""
        err = (error_msg or "").lower()
        keywords = (
            "rate limit", "rate_limit", "429", "529", "resource_exhausted",
            "quota", "token limit", "tokens", "out of tokens", "token budget",
            "exceeded your current quota", "insufficient_quota", "credit balance",
            "usage limit", "capacity", "overloaded", "exhausted",
            "cli exited with code 3", "code 3", "exit status 3",
        )
        return any(k in err for k in keywords)

    def is_task_complex(self, prompt: str, task_mode: Optional[str] = None) -> bool:
        """Evaluates whether an engineering, code, or analytical task requires high reasoning
        (Gemini 3.8 Flash High) versus standard/moderate reasoning (Gemini 3.8 Flash Medium).

        Complex Tasks -> True (Flash High):
        - Architecture, system design, concurrency, threading, asyncio, deadlocks, race conditions.
        - Multi-file refactors, deep codebase audits, self-evolution, sandbox security.
        - Complex mathematical proofs, algorithms, data structures, formal verification.
        - Long structured prompts (>25 words with code blocks or numbered steps).

        Moderate/Standard Tasks -> False (Flash Medium):
        - Routine single-file inspections, git status, git diff, list directory.
        - Running test suites, checking test outputs, simple assertion fixes.
        - Conversational follow-ups in an active code session ("yes", "proceed", "fix that").
        - Direct code explanation questions ("what does this function do?").
        """
        p = (prompt or "").lower().strip()
        if not p:
            return False

        # 1. Structural multi-file refactoring, architecture, and concurrency keywords
        complex_patterns = [
            r"\b(architecture|system\s+design|architectural|concurrency|multithread(ed|ing)?|thread\s+safety|race\s+condition|deadlock|event\s+loop|asyncio)\b",
            r"\b(self-evolution|self-build|evolve|sandbox|security\s+audit|vulnerability|threat\s+model)\b",
            r"\b(refactor\s+(the\s+entire|multiple|all|codebase)|overhaul|rewrite|redesign|restructure)\b",
            r"\b(optimize\s+yourself|enhance\s+yourself|modify\s+yourself|edit\s+yourself|build\s+yourself)\b",
            r"\b(algorithm|data\s+structure|proof|prove|benchmark|optimization\s+strategy|memory\s+leak|profil(er|ing)|tracemalloc|py-spy)\b",
            r"\b(math|equation|calculate|derivation|derive|formal\s+verification)\b",
            r"\b(multi-file|several\s+files|across\s+the\s+project|entire\s+repo|entire\s+repository)\b",
        ]
        if any(re.search(pat, p) for pat in complex_patterns):
            return True

        # 2. Numbered multi-step instructions or long detailed engineering directives
        has_steps = bool(re.search(r"(\bstep\s*\d|\b\d+\.\s+[a-z]|\bfirst[,:]\s+|\bsecond[,:]\s+)", p))
        word_count = len(p.split())
        if has_steps and word_count >= 15:
            return True

        if word_count > 35 and ("```" in p or "def " in p or "class " in p or "error" in p or "traceback" in p):
            return True

        return False

    def stop(self):
        """Cleanly terminates the persistent warm daemon and any child processes."""
        if self.daemon:
            self.daemon.stop()

    def __del__(self):
        self.stop()

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

    def get_candidate_cascade(self, model: str, prompt: str = "") -> List[str]:
        """Builds a priority fallback cascade within the Google AI Pro fleet.
        If Claude Sonnet is selected and token-exhausted or in fallback, dynamically
        prioritizes Gemini 3.8 Flash High vs Flash Medium based on task complexity.
        """
        resolved = self.resolve_model(model)
        if "argon" in resolved:
            pool = ["gemini-4-argon-high", "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]
        elif resolved == "gemini-3.8-flash-low":
            pool = ["gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high"]
        elif resolved == "gemini-3.8-flash-medium":
            pool = ["gemini-3.8-flash-medium", "gemini-3.8-flash-high", "gemini-3.8-flash-low"]
        elif resolved == "gemini-3.8-flash-high":
            pool = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]
        elif "claude" in resolved:
            is_complex = self.is_task_complex(prompt) if prompt else True
            flash_fallback = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium"] if is_complex else ["gemini-3.8-flash-medium", "gemini-3.8-flash-high"]
            if self.is_claude_exhausted():
                pool = flash_fallback + [resolved, "claude-opus-4-6-thinking"]
            else:
                pool = list(self.TIER_CLAUDE) + flash_fallback
        else:
            pool = [resolved, "gemini-3.8-flash-medium", "gemini-3.8-flash-high", "gemini-3.8-flash-low"]

        # If Claude is exhausted, return pool directly where Flash fallback is first
        if "claude" in resolved and self.is_claude_exhausted():
            return pool

        # Ensure requested model is first
        ordered = [resolved] + [m for m in pool if m != resolved]
        return ordered

    @staticmethod
    def _matches_any_keyword(text: str, keywords: List[str]) -> bool:
        """Matches whole words or exact phrases against lowercase text."""
        for kw in keywords:
            if " " in kw or kw.endswith(" "):
                if kw in text:
                    return True
            else:
                if re.search(r"\b" + re.escape(kw) + r"\b", text):
                    return True
        return False

    def classify_prompt_tier(self, prompt: str, task_mode: Optional[str] = None) -> str:
        """Determines optimal Google AI Pro model tier based on semantic intent, structural context,
        and latency constraints.

        Tiering Architecture:
        - Fast Voice & Reflexes -> Gemini 3.8 Flash Low (sub-second speech turns, device vitals, greetings).
        - Software Engineering & Code -> Claude Sonnet 4.6 (structural code syntax, AST/git/regex, active code tasks).
        - Deep Reasoning & Math -> Gemini 3.8 Flash High (mathematics, proofs, architectural analysis, complex planning).
        - Butler Workhorse & General QA -> Gemini 3.8 Flash Medium (default warm daemon, email drafting, notes, summaries).
        """
        p = prompt.lower().strip()

        # 1. Fast Voice, Quick Device Reflexes, Weather/Time/Vitals, Greetings -> Gemini 3.8 Flash Low
        # Handled first to ensure sub-50ms conversational reflex latency even during coding or planning.
        reflex_patterns = [
            r"\b(weather|temperature|forecast)\b",
            r"\b(what('s|\s+is)?\s+the\s+time|what\s+time\s+is\s+it|current\s+time|^time\??$)\b",
            r"\b(what('s|\s+is)?\s+(the\s+|today'?s\s+)?date|current\s+date|^date\??$)\b",
            r"\b(battery|charging\s+status|cpu\s+usage|ram\s+usage|gpu\s+usage|system\s+vitals)\b",
            r"\b(volume|mute|unmute|pause\s+(music|playback)|resume\s+(music|playback)|skip\s+song|next\s+track)\b",
            r"\b(hello|hi|hey|good\s+morning|good\s+afternoon|good\s+evening|how\s+are\s+you|who\s+are\s+you|thank\s+you|thanks)\b",
        ]
        if any(re.search(pat, p) for pat in reflex_patterns):
            return "gemini-3.8-flash-low"

        # 2. Inherited Task Momentum: If Sir is in an active code session and this is a follow-up turn
        if task_mode == "code":
            cancel_triggers = ("stop", "cancel", "never mind", "nevermind", "exit", "reset", "switch to general")
            if not any(trigger in p for trigger in cancel_triggers):
                if self.is_claude_exhausted():
                    is_complex = self.is_task_complex(prompt, task_mode)
                    fallback = "gemini-3.8-flash-high" if is_complex else "gemini-3.8-flash-medium"
                    logger.info("Claude Sonnet token-exhausted; routing to %s (complexity=%s)", fallback, "complex" if is_complex else "moderate")
                    return fallback
                return "claude-sonnet-4-6"

        # 3. Structural Code & Technical Engineering -> Claude Sonnet 4.6
        # Uses structural syntax, token boundaries, and developer tools to eliminate false-positive
        # collisions on words like 'script', 'patch', or 'syntax' in casual conversation.
        code_structural_patterns = [
            r"```",                                                                        # Markdown code block
            r"\b(def|class|async\s+def)\s+[a-zA-Z_]\w*\s*(\(|:)",                          # Python definition
            r"\bfrom\s+[a-zA-Z_][\w.]*\s+import\s+[a-zA-Z_*]\w*",                          # From ... import ... (supports dotted paths)
            r"(^\s*import\s+[a-zA-Z_][\w.]*|;\s*import\s+[a-zA-Z_][\w.]*|\bimport\s+(sys|os|re|json|math|time|datetime|subprocess|unittest|pytest|numpy|pandas|torch|requests|typing)\b)", # Import statement
            r"\b(elif\s+.*:|except\s+[a-zA-Z_]\w*(\s+as\s+\w+)?\s*:|except\s*:|finally\s*:|try\s*:|raise\s+[a-zA-Z_]\w*error|return\s+(true|false|none|self\b|[0-9]+))", # Control flow & exceptions
            r"\bgit\s+(status|diff|commit|push|pull|branch|checkout|log|merge|rebase|stash|fetch|reset)\b", # Git CLI
            r"\b(pytest|unittest|mock\.patch|mock\.mock|magicmock|assert[a-z]\w*|assertequal|asserttrue|assertfalse)\b", # Testing suites
            r"\b(traceback|stack\s*trace|nullpointerexception|syntaxerror|typeerror|attributeerror|keyerror|nameerror|indexerror|zerodivisionerror)\b", # Traceback/errors
            r"\b(powershell|pip\s+install|npm\s+install|pip\s+list|pip\s+uninstall|npm\s+run)\b", # Shell / Package managers
            r"\b(regex|regular\s+expression|ast\b|refactor\s+code|refactor\s+this|debug\s+this|debug\s+the|write\s+a\s+function|write\s+code|write\s+a\s+script|python\s+script|bash\s+script|shell\s+script|fix\s+the\s+bug|fix\s+this\s+bug|unit\s+tests?|edit\s+yourself|enhance\s+yourself|optimize\s+yourself|modify\s+yourself|self-build|self-evolution|jarvis_2\.0)\b",
        ]
        if any(re.search(pat, p) for pat in code_structural_patterns):
            if self.is_claude_exhausted():
                is_complex = self.is_task_complex(prompt, task_mode)
                fallback = "gemini-3.8-flash-high" if is_complex else "gemini-3.8-flash-medium"
                logger.info("Claude Sonnet token-exhausted; routing to %s (complexity=%s)", fallback, "complex" if is_complex else "moderate")
                return fallback
            return "claude-sonnet-4-6"

        # 4. Hard Reasoning, Math, Logic, Planning, Dynamic Evolution -> Gemini 3.8 Flash High
        # Semantic intent based on problem complexity, mathematical rigor, and architectural trade-offs.
        # Arbitrary word-count thresholds (>30 words) are strictly eliminated to avoid wasting 12s on mundane questions.
        high_reasoning_words = [
            "analyze", "analysis", "strategy", "why does", "math", "calculate",
            "solve", "syllabus", "timetable", "essay", "architecture", "compare",
            "proof", "prove", "algorithm", "benchmark", "evaluate", "custom skill",
            "create skill", "self build", "evolve", "conflict resolution", "derivation",
            "derive", "equation", "logic puzzle", "trade-offs", "curriculum"
        ]
        if self._matches_any_keyword(p, high_reasoning_words):
            return "gemini-3.8-flash-high"

        # 5. Standard Everyday Butler Tasks, Drafting, Summaries, General QA -> Gemini 3.8 Flash Medium
        # Default workhorse engine. Maintains sticky daemon affinity and Google AI Pro's 30,000+ token KV prompt cache.
        return "gemini-3.8-flash-medium"

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
        cascade_models = candidates or self.get_candidate_cascade(target_model, prompt=full_prompt)
        last_error_res = None

        # For voice streaming turns, prepend spoken voice guidance to suppress unprompted internal chain-of-thought
        if sentence_callback and "[Spoken Voice Guidance:" not in full_prompt:
            voice_hint = (
                "[Spoken Voice Guidance: Deliver a direct, concise spoken response in Paul Bettany's natural British cadence. "
                "Omit markdown formatting, bulleted lists, and unprompted internal chain-of-thought monologue. "
                "When Sir asks for your opinion, appraisal, assessment, or thoughts on the project, synthesize and deliver your direct architectural evaluation immediately using your built-in architectural blueprint and repository self-knowledge without executing exploratory shell commands or file scans.]\n\n"
            )
            full_prompt = voice_hint + full_prompt

        # Fast-path: Execute via persistent warm daemon if available and no specific conversation_id override
        effective_daemon_model = cascade_models[0] if (cascade_models and "claude" in target_model and self.is_claude_exhausted()) else target_model
        if self.use_daemon and self.daemon and effective_daemon_model in cascade_models and not conversation_id:
            daemon_effort = None
            if "-high" in effective_daemon_model:
                daemon_effort = "high"
            elif "-low" in effective_daemon_model:
                daemon_effort = "low"
            elif "-medium" in effective_daemon_model:
                daemon_effort = "medium"
            elif "claude" in effective_daemon_model:
                daemon_effort = None
            elif self.effort:
                daemon_effort = self.effort

            try:
                daemon_res = self.daemon.execute_turn(
                    prompt=full_prompt,
                    model=effective_daemon_model,
                    effort=daemon_effort,
                    sentence_callback=sentence_callback,
                    timeout=timeout,
                )
                if daemon_res and daemon_res.get("status") == "SUCCESS":
                    self.last_latency_ms = float(daemon_res.get("duration_seconds", 0.0)) * 1000.0
                    self.last_conversation_id = daemon_res.get("conversation_id")
                    return daemon_res
                elif daemon_res and daemon_res.get("status") == "ERROR":
                    daemon_err = daemon_res.get("response") or daemon_res.get("error") or ""
                    if "claude" in effective_daemon_model and self._is_token_exhaustion_error(daemon_err):
                        self.mark_claude_exhausted(duration_seconds=900.0, reason=daemon_err)
                        is_complex = self.is_task_complex(full_prompt)
                        chosen_fallback = "gemini-3.8-flash-high" if is_complex else "gemini-3.8-flash-medium"
                        logger.warning(
                            "Claude Sonnet daemon quota limit reached: '%s'. Task complexity evaluated as '%s' -> prioritizing %s in fallback cascade...",
                            daemon_err[:80],
                            "COMPLEX" if is_complex else "MODERATE",
                            chosen_fallback
                        )
                        if chosen_fallback in cascade_models:
                            cascade_models = [m for m in cascade_models if m != chosen_fallback]
                            cascade_models.insert(0, chosen_fallback)
            except Exception as d_err:
                logger.debug("Persistent daemon turn encountered error: %s", d_err)

            logger.debug("Persistent daemon fast-path bypassed or failed; cascading to one-shot execution.")

        attempt_idx = 0
        while attempt_idx < len(cascade_models):
            attempt_model = cascade_models[attempt_idx]
            attempt_idx += 1
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
            err_text = res.get("response", "")

            # If Claude failed due to token or quota limits, mark Claude as exhausted and prioritize Flash High/Medium
            if "claude" in attempt_model and self._is_token_exhaustion_error(err_text):
                self.mark_claude_exhausted(reason=err_text)
                is_complex = self.is_task_complex(full_prompt)
                chosen_fallback = "gemini-3.8-flash-high" if is_complex else "gemini-3.8-flash-medium"
                logger.warning(
                    "Claude Sonnet token/quota limit reached. Task complexity evaluated as '%s' -> prioritizing %s in fallback cascade...",
                    "COMPLEX" if is_complex else "MODERATE",
                    chosen_fallback
                )
                if chosen_fallback in cascade_models:
                    # Move chosen_fallback to immediately next position
                    remaining = [m for m in cascade_models[attempt_idx:] if m != chosen_fallback]
                    cascade_models = cascade_models[:attempt_idx] + [chosen_fallback] + remaining

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
        effort = None
        if "-high" in model:
            effort = "high"
        elif "-low" in model:
            effort = "low"
        elif "-medium" in model:
            effort = "medium"
        elif "claude" in model:
            effort = None
        elif self.effort:
            effort = self.effort

        cmd = [
            self.agy_path,
            "--model", model,
            "--disable-slash-commands",
            "--output-format", "json",
        ]
        if effort:
            cmd.extend(["--effort", effort])
        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        t_start = time.perf_counter()
        try:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                encoding="utf-8",
                timeout=timeout,
                creationflags=creation_flags,
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
        effort = None
        if "-high" in model:
            effort = "high"
        elif "-low" in model:
            effort = "low"
        elif "-medium" in model:
            effort = "medium"
        elif "claude" in model:
            effort = None
        elif self.effort:
            effort = self.effort

        cmd = [
            self.agy_path,
            "--model", model,
            "--disable-slash-commands",
            "--output-format", "stream-json",
        ]
        if effort:
            cmd.extend(["--effort", effort])
        if conversation_id:
            cmd.extend(["--conversation", conversation_id])

        t_start = time.perf_counter()
        accumulated_text = []
        speech_buffer = ""
        final_result_data: Dict[str, Any] = {}

        try:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=creation_flags,
            )

            # Send prompt and close stdin to signal EOF
            proc.stdin.write(prompt)
            proc.stdin.close()

            # Sir directive: zero time restrictions on LLM reasoning and tasks. Run to natural completion.
            try:
                # Read NDJSON lines in real time
                for line in iter(proc.stdout.readline, ""):
                    last_activity_time = time.perf_counter()
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

            except (ValueError, OSError) as pipe_err:
                logger.warning("Antigravity stream pipe error: %s", pipe_err)

            proc.stdout.close()
            stderr_out = ""
            if proc.stderr:
                try:
                    stderr_out = proc.stderr.read().strip()
                except Exception:
                    pass
            proc.wait(timeout=5.0)

            if proc.returncode != 0:
                err_msg = stderr_out or f"CLI exited with code {proc.returncode}"
                logger.warning("Antigravity streaming invocation error (%s): %s", model, err_msg)
                return {
                    "status": "ERROR",
                    "response": f"Antigravity CLI error: {err_msg}",
                    "duration_seconds": time.perf_counter() - t_start,
                    "usage": {},
                }

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
        m_term = re.search(r'(?<!\d)(?<!\b[A-Za-z])[.!?\n]+(?!\d)(\s+|$)', buffer)
        m_clause = re.search(r'(?:[,;—]|(?<!\d):(?!\d))(\s+|$)', buffer)

        candidates = []
        if m_term and m_term.start() >= 2:
            candidates.append((m_term.end(), m_term.start()))
        if m_clause and m_clause.start() >= 3:
            prefix_cand = buffer[:m_clause.start()].strip()
            if prefix_cand and len(prefix_cand.split()) >= 1:
                candidates.append((m_clause.end(), m_clause.start()))

        if candidates:
            candidates.sort(key=lambda x: x[1])
            cut_idx = candidates[0][0]
            prefix = buffer[:cut_idx].strip()
            if prefix:
                return prefix, buffer[cut_idx:]

        if len(buffer) >= 80:
            last_space = buffer.rfind(" ")
            if last_space > 20:
                return buffer[:last_space].strip(), buffer[last_space:]

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
