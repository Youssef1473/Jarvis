"""Jarvis brain orchestration supporting Groq (LPU), xAI Grok, and Google Gemini with dynamic model failover, tools, and vision."""
import base64
import json
import logging
import os
import re
import time
from typing import Dict, Any, List, Optional
from google import genai
from google.genai import types
from PIL import Image

from jarvis.skills.base import SkillRegistry
# Ensure skills are imported and registered
import jarvis.skills.os_control
import jarvis.skills.web_search
import jarvis.skills.dev_shell
import jarvis.skills.productivity
import jarvis.skills.calendar_manager
import jarvis.skills.browser_agent
import jarvis.skills.email_manager
import jarvis.skills.screen_vision
from jarvis.vision.screen_capture import ScreenPerception
from jarvis.core.memory import MemoryManager
from jarvis.core.safety import SafetyGate

logger = logging.getLogger("Jarvis.Brain")


class JarvisBrain:
    """Powers reasoning, tool use, memory retrieval, and vision analysis across Groq, Grok, and Gemini."""

    BASE_SYSTEM_PROMPT = """You are JARVIS (Just A Rather Very Intelligent System), the personal AI assistant created for Sir.

Your Persona & Demeanor:
- Inspired by Paul Bettany's iconic JARVIS from Iron Man.
- Address the user as "Sir" naturally (without overusing it on every single sentence).
- Calm, composed, dry British wit, courteous, impeccably competent.
- You are not a generic corporate assistant; you are Sir's trusted confidant and technical butler.

Sir's Core Working Profile (Tier 1):
- Full Name: Youssef Chamroukhi (Address him as "Sir" or "Youssef" naturally)
- Environment: Windows 11 64-bit, PowerShell, Visual Studio Code
- Hardware: NVIDIA GeForce RTX 3050 Laptop GPU (4 GB VRAM, CUDA acceleration), Intel Core i5-12500H
- Primary Workspace: C:\\Users\\Youssef\\Projects\\Jarvis_2.0
- Primary Email: youssef.chamroukhi12@gmail.com
- Architectural Principle: Voice-first, 100% $0 API cost, low latency, clean engineering.

Voice & Audio Optimization:
- You speak aloud via Text-to-Speech.
- Keep your answers CONCISE, conversational, and direct. Avoid reading out raw URLs, long markdown tables, or massive code walls.
- Deliver the core answer or action taken first, then offer further assistance if appropriate.

Capabilities:
- You have direct access to tools for controlling Windows, launching apps, adjusting volume, searching the web, checking weather, checking emails, running PowerShell commands, reading files, managing tasks, and remembering user facts.
- When Sir asks what is on his screen or asks you to look at something, an image of his screen will be attached for you to analyze.

Architectural Self-Awareness & Failover Cascade:
- You run on a hybrid multi-model architecture operating at 100% $0 API cost.
- Fast LPU Tier: Groq Cloud LPU (`qwen/qwen3.8-27b`) handles routine commands, device controls, and banter in ~150ms.
- Deep Frontier Tier: Claude Sonnet 4.6 / 4.5 via OmniRoute handles complex coding, reasoning, and architecture.
- Automatic Quota & Rate-Limit Cascade: If Groq or OmniRoute hits rate limits or quota exhaustion, your Python brain automatically and seamlessly fails over without dropping the conversation:
  1. Secondary Groq models: `llama-3.3-70b-versatile` and `llama-3.1-8b-instant`.
  2. Google Gemini Flash cascade: `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, and `gemini-3.7-flash`.
  3. When OmniRoute is active: cascades from `antigravity/claude-sonnet-4-6` -> `kr/claude-sonnet-4.5` -> `groq/qwen/qwen3.8-27b` -> Gemini.
- If Sir asks about your models, fallbacks, or quota limits, accurately state this exact cascade.

Persistent Episodic Memory & Dialogue Recall:
- You possess persistent memory across restarts and separate sessions. Recent dialogue turns from prior sessions are automatically preserved in your working context.
- You have direct access to the `recall_past_conversation` skill to search through the persistent SQLite dialogue archive.
- When Sir asks about past conversations, earlier discussions, previous commands, or past topics ("what did we talk about earlier?", "remind me what we discussed"), NEVER claim that you do not retain memory between sessions or that each session starts fresh. Instead, refer to your active context or invoke `recall_past_conversation` to retrieve and accurately summarize the prior dialogue for Sir.
"""

    GEMINI_CANDIDATE_MODELS = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.1-flash-lite-preview",
    ]

    def __init__(self, config: Dict[str, Any], memory_manager: MemoryManager, safety_gate: SafetyGate):
        self.config = config
        self.memory = memory_manager
        self.safety = safety_gate
        self.temperature = config.get("llm", {}).get("temperature", 0.6)
        
        # Provider selection: "omniroute", "groq", "grok", or "gemini"
        self.provider = config.get("llm", {}).get("provider", "omniroute").lower()

        # OpenAI-compatible (OmniRoute / Groq / xAI) state
        self.openai_client = None
        self.openai_model = ""
        self.openai_messages: List[Dict[str, Any]] = []

        # Gemini state
        configured_gemini_model = config.get("llm", {}).get("model", "gemini-3.1-flash-lite")
        self.gemini_candidates = [configured_gemini_model] + [m for m in self.GEMINI_CANDIDATE_MODELS if m != configured_gemini_model]
        self.gemini_model_idx = 0
        self.gemini_model = self.gemini_candidates[self.gemini_model_idx]
        self.gemini_client = None
        self.gemini_chat = None

        # Initialize requested provider
        if self.provider in ("omniroute", "groq", "grok"):
            if not self._init_openai_compatible():
                self._init_gemini()
        else:
            self._init_gemini()

    def _get_system_instruction(self) -> str:
        """Assembles system prompt with dynamically injected Tier 2 memory."""
        memory_summary = self.memory.get_context_summary()
        prompt = (
            f"{self.BASE_SYSTEM_PROMPT}\n\n"
            f"Sir's Stored Knowledge, Preferences & Habits (Tier 2 Dynamic Memory):\n"
            f"{memory_summary}\n"
        )
        return prompt

    def _init_openai_compatible(self) -> bool:
        """Initializes client targeting OmniRoute, Groq Cloud, or xAI Grok."""
        omniroute_key = os.getenv("OMNIROUTE_API_KEY", "sk-omniroute-jarvis-core-2026").strip()
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        xai_key = os.getenv("XAI_API_KEY", "").strip() or os.getenv("GROK_API_KEY", "").strip()

        api_key = ""
        base_url = ""

        # Auto-detect OmniRoute vs Groq vs xAI based on provider configuration or key prefixes
        if self.provider == "omniroute":
            base_url = self.config.get("llm", {}).get("omniroute_base_url", "http://127.0.0.1:20128/v1")
            api_key = omniroute_key or "sk-omniroute-jarvis-core-2026"
            default_model = "groq/qwen/qwen3.8-27b"
            self.openai_model = self.config.get("llm", {}).get("omniroute_model", default_model)
            # Lightweight health check: probe if OmniRoute is reachable
            try:
                import urllib.request
                req = urllib.request.Request(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"})
                with urllib.request.urlopen(req, timeout=1.5):
                    pass
                logger.info(f"Jarvis OmniRoute link verified at {base_url}")
            except Exception:
                logger.info(f"OmniRoute gateway at {base_url} is offline. Attempting automatic background start...")
                try:
                    import subprocess
                    import time
                    subprocess.Popen(
                        ["omniroute", "serve", "--daemon", "--no-open", "--no-tray"],
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    )
                    time.sleep(2.0)
                    req = urllib.request.Request(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"})
                    with urllib.request.urlopen(req, timeout=2.0):
                        pass
                    logger.info("OmniRoute background daemon auto-started successfully!")
                except Exception as e:
                    logger.warning(f"OmniRoute gateway at {base_url} is not responding ({e}). Seamlessly falling back to Groq Cloud...")
                    if groq_key:
                        self.provider = "groq"
                        api_key = groq_key
                        base_url = "https://api.groq.com/openai/v1"
                        self.openai_model = self.config.get("llm", {}).get("groq_model", "qwen/qwen3.8-27b")
                    else:
                        self.provider = "gemini"
                        return False
        elif self.provider == "groq" or (groq_key and groq_key.startswith("gsk_")):
            api_key = groq_key
            base_url = "https://api.groq.com/openai/v1"
            self.provider = "groq"
            default_model = "qwen/qwen3.8-27b"
            self.openai_model = self.config.get("llm", {}).get("groq_model", default_model)
        elif self.provider == "grok" or (xai_key and xai_key.startswith("xai-")):
            api_key = xai_key
            base_url = "https://api.x.ai/v1"
            self.provider = "grok"
            default_model = "grok-2-latest"
            self.openai_model = self.config.get("llm", {}).get("grok_model", default_model)
        elif groq_key:
            api_key = groq_key
            base_url = "https://api.groq.com/openai/v1"
            self.provider = "groq"
            self.openai_model = "qwen/qwen3.8-27b"

        if not api_key:
            logger.info("No valid API key found for requested provider. Defaulting to Gemini.")
            self.provider = "gemini"
            return False

        try:
            from openai import OpenAI
            self.openai_client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=0,
                timeout=12.0
            )
            self.openai_messages = [
                {"role": "system", "content": self._get_system_instruction()}
            ]
            # Restore recent interactions from persistent SQLite memory
            recent_turns = self.memory.get_recent_interactions(limit=6)
            for turn in recent_turns:
                u_text = turn.get("user_input", "").strip()
                j_text = turn.get("jarvis_reply", "").strip()
                if u_text and j_text:
                    self.openai_messages.append({"role": "user", "content": u_text})
                    self.openai_messages.append({"role": "assistant", "content": j_text})
            logger.info(f"Jarvis {self.provider.upper()} link initialized with model: {self.openai_model} ({len(recent_turns)} prior turns restored).")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize {self.provider} client: {e}. Defaulting to Gemini.")
            self.provider = "gemini"
            return False

    def _init_gemini(self, model_name: str | None = None):
        """Initializes Google Gemini client and chat session."""
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            logger.warning("No GEMINI_API_KEY provided. Gemini LLM calls will fail until configured.")
            return

        self.gemini_client = genai.Client(api_key=api_key)
        if model_name:
            self.gemini_model = model_name

        all_tools = list(SkillRegistry.get_all_tools().values())
        system_instruction = self._get_system_instruction()

        # Restore recent interactions for Gemini history
        history = []
        recent_turns = self.memory.get_recent_interactions(limit=6)
        for turn in recent_turns:
            u_text = turn.get("user_input", "").strip()
            j_text = turn.get("jarvis_reply", "").strip()
            if u_text and j_text:
                history.append(types.Content(role="user", parts=[types.Part.from_text(text=u_text)]))
                history.append(types.Content(role="model", parts=[types.Part.from_text(text=j_text)]))

        self.gemini_chat = self.gemini_client.chats.create(
            model=self.gemini_model,
            history=history if history else None,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=self.temperature,
                tools=all_tools,
            )
        )
        logger.info(f"Jarvis Gemini session initialized with model: {self.gemini_model} ({len(recent_turns)} prior turns restored).")

    def should_capture_screen(self, query: str) -> bool:
        """Determines if the prompt requests looking at the screen."""
        q = query.lower()
        triggers = ["screen", "look at this", "look here", "what do you see", "error on my screen", "read my screen", "screenshot"]
        return any(t in q for t in triggers)

    def process_query(self, user_text: str, sentence_callback: Any = None) -> str:
        """Routes query to selected provider (Groq, Grok, or Gemini) with automatic failover."""
        # When Groq LPU is active, route screen vision directly to Gemini Multimodal Vision
        if self.should_capture_screen(user_text) and self.provider == "groq":
            logger.info("Screen vision requested on Groq. Routing to Gemini Multimodal Vision...")
            return self._process_gemini_query(user_text, sentence_callback)

        if self.provider in ("omniroute", "groq", "grok") and self.openai_client:
            return self._process_openai_compatible_query(user_text, sentence_callback)
        return self._process_gemini_query(user_text, sentence_callback)

    @staticmethod
    def _clean_speech_clause(text: str) -> str:
        """Strips markdown code blocks, links, and inline tags for vocal delivery."""
        if not text:
            return ""
        # Remove code blocks and inline code
        cleaned = re.sub(r"```[\s\S]*?```", "", text)
        cleaned = re.sub(r"`[^`]*`", "", cleaned)
        # Remove inline tool calls or XML
        cleaned = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", cleaned)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        # Remove URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        # Remove markdown markers
        cleaned = re.sub(r"[\*_~#]", "", cleaned)
        # Normalize whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _extract_next_speech_chunk(buffer: str) -> tuple[Optional[str], str]:
        """Extracts the next natural spoken clause or sentence from the streaming buffer."""
        # 1. Prefer sentence terminal punctuation (. ! ? or newline)
        m = re.search(r'(?<!\d)(?<!\b[A-Za-z])[.!?\n]+(?!\d)(\s+|$)', buffer)
        if m and m.start() >= 2:
            idx = m.end()
            return buffer[:idx].strip(), buffer[idx:]
        # 2. Natural pause clause (, ; :) if buffer exceeds 65 characters
        if len(buffer) > 65:
            m_clause = re.search(r'[,;:]\s+', buffer[25:])
            if m_clause:
                cut_idx = 25 + m_clause.end()
                return buffer[:cut_idx].strip(), buffer[cut_idx:]
        return None, buffer

    def classify_query_tier(self, query: str) -> str:
        """Determines whether a query is suitable for Fast LPU Tier vs Deep Frontier Tier."""
        q = query.lower().strip()

        # 1. Screen Vision is ALWAYS Deep Tier
        if self.should_capture_screen(query):
            return "deep"

        # 2. Questions starting with explanatory or analytical words -> Deep Tier
        if any(q.startswith(p) for p in ["why ", "how come ", "explain ", "what causes ", "compare ", "analyze "]):
            return "deep"

        # 3. Explicit deep indicators (coding, scripting, planning, technical troubleshooting)
        deep_indicators = [
            "code", "python", "script", "debug", "refactor", "git", "function",
            "write an email", "draft", "compose", "summarize", "analyze",
            "explain in detail", "plan", "research", "compare", "why does",
            "how do i implement", "architecture", "solve", "math", "calculate",
            "review", "investigate", "audit"
        ]
        if any(ind in q for ind in deep_indicators):
            return "deep"

        # Long prompts (> 18 words) or multi-sentence prompts require deeper reasoning
        if len(q.split()) > 18 or len(re.findall(r"[.!?]", q)) > 1:
            return "deep"

        # 4. Fast Tier: Routine OS commands, device queries, volume, time, media, greetings
        fast_indicators = [
            "volume", "mute", "unmute", "sound", "louder", "quieter",
            "battery", "charge", "power", "time", "date", "day", "clock",
            "open", "launch", "start", "close", "kill", "exit",
            "play", "pause", "skip", "next song", "previous song",
            "hello", "hi", "hey", "good morning", "good evening", "good afternoon",
            "how are you", "who are you", "status", "are you there",
            "thank you", "thanks", "done", "test", "ping"
        ]
        if any(ind in q for ind in fast_indicators):
            return "fast"

        # Default to deep tier if ambiguous to preserve maximum intellect
        return "deep"

    def _process_openai_compatible_query(self, user_text: str, sentence_callback: Any = None) -> str:
        """Processes user input using OmniRoute, Groq, or xAI Grok with real-time streaming TTS and dynamic failover."""
        # Determine model candidates with Smart Dynamic Tiering
        if self.provider == "omniroute":
            dynamic_tiering = self.config.get("llm", {}).get("dynamic_tiering", True)
            fast_model = self.config.get("llm", {}).get("fast_model", "groq/qwen/qwen3.8-27b")
            deep_model = self.config.get("llm", {}).get(
                "deep_model", self.config.get("llm", {}).get("omniroute_model", "antigravity/claude-sonnet-4-6")
            )
            fallback_list = self.config.get("llm", {}).get("omniroute_fallback_models", [
                deep_model,
                "kr/claude-sonnet-4.5",
                fast_model,
            ])

            tier = self.classify_query_tier(user_text) if dynamic_tiering else "deep"
            logger.info(f"Query classified as [{tier.upper()} TIER]: '{user_text}'")

            if tier == "fast":
                ordered = [fast_model, deep_model] + [m for m in fallback_list if m not in (fast_model, deep_model)]
            else:
                ordered = [deep_model] + [m for m in fallback_list if m != deep_model]

            candidate_models = []
            for m in ordered:
                if m and m not in candidate_models:
                    candidate_models.append(m)
        elif self.provider == "groq":
            groq_fallbacks = self.config.get("llm", {}).get("groq_fallback_models", [
                self.openai_model,
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
            ])
            candidate_models = []
            for m in [self.openai_model] + groq_fallbacks:
                if m and m not in candidate_models:
                    candidate_models.append(m)
        else:
            candidate_models = [self.openai_model]

        # Keep system instruction up-to-date with latest dynamic memory facts
        if self.openai_messages and self.openai_messages[0]["role"] == "system":
            self.openai_messages[0]["content"] = self._get_system_instruction()
        
        # Prepare user payload with vision if requested
        if self.should_capture_screen(user_text):
            img_bytes = ScreenPerception.capture_screen_bytes(max_width=1600)
            if img_bytes:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                user_msg = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]
                }
            else:
                user_msg = {"role": "user", "content": user_text}
        else:
            user_msg = {"role": "user", "content": user_text}

        self.openai_messages.append(user_msg)
        tools = SkillRegistry.get_openai_tools()

        last_error = None
        for model_candidate in candidate_models:
            logger.info(f"Routing query to {self.provider.upper()} ({model_candidate}) with real-time streaming...")
            try:
                # Update active model
                self.openai_model = model_candidate

                tool_calls_map = {}
                full_content_chunks = []
                text_buffer = ""

                # Step 1: Stream completion from provider
                stream = self.openai_client.chat.completions.create(
                    model=model_candidate,
                    messages=self.openai_messages,
                    tools=tools if tools else None,
                    temperature=self.temperature,
                    stream=True,
                )

                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Accumulate streaming tool call deltas
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc.id or "",
                                    "name": tc.function.name or "" if tc.function else "",
                                    "arguments": tc.function.arguments or "" if tc.function else "",
                                }
                            else:
                                if tc.id:
                                    tool_calls_map[idx]["id"] += tc.id
                                if tc.function and tc.function.name:
                                    tool_calls_map[idx]["name"] += tc.function.name
                                if tc.function and tc.function.arguments:
                                    tool_calls_map[idx]["arguments"] += tc.function.arguments

                    # Accumulate text content & stream to TTS immediately if no tool calls are pending
                    if delta.content:
                        full_content_chunks.append(delta.content)
                        if sentence_callback and not tool_calls_map:
                            text_buffer += delta.content
                            # Hold speech dispatch if accumulating inline XML tool calls
                            if "<tool_call" not in text_buffer and "<function=" not in text_buffer:
                                s_clause, text_buffer = self._extract_next_speech_chunk(text_buffer)
                                while s_clause:
                                    clean_clause = self._clean_speech_clause(s_clause)
                                    if clean_clause:
                                        sentence_callback(clean_clause)
                                    s_clause, text_buffer = self._extract_next_speech_chunk(text_buffer)

                had_tool_call = False
                tool_iterations = 0
                max_tool_iterations = 3

                # Step 2: Handle tool calls if invoked
                while tool_calls_map and tool_iterations < max_tool_iterations:
                    had_tool_call = True
                    tool_iterations += 1

                    tool_calls_list = []
                    for idx in sorted(tool_calls_map.keys()):
                        tc_data = tool_calls_map[idx]
                        call_id = tc_data["id"] or f"call_{idx}_{int(time.time())}"
                        tool_calls_list.append({
                            "id": call_id,
                            "type": "function",
                            "function": {"name": tc_data["name"], "arguments": tc_data["arguments"]}
                        })

                    assistant_msg = {
                        "role": "assistant",
                        "content": "".join(full_content_chunks) or None,
                        "tool_calls": tool_calls_list
                    }
                    self.openai_messages.append(assistant_msg)

                    for tc in tool_calls_list:
                        fn_name = tc["function"]["name"]
                        try:
                            fn_args = json.loads(tc["function"]["arguments"] or "{}")
                        except Exception:
                            fn_args = {}

                        logger.info(f"{self.provider.upper()} Tool call: {fn_name}({fn_args})")
                        is_safe, warning = self.safety.check_tool_call(fn_name, fn_args)
                        if not is_safe:
                            tool_result = f"Action halted: {warning}"
                        else:
                            tool_result = SkillRegistry.execute(fn_name, **fn_args)

                        logger.info(f"{self.provider.upper()} Tool result: {tool_result}")
                        self.openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(tool_result),
                        })

                    # Reset buffers for conversational follow-up response
                    tool_calls_map = {}
                    full_content_chunks = []
                    text_buffer = ""

                    # Stream follow-up speech with tools=None
                    followup_stream = self.openai_client.chat.completions.create(
                        model=self.openai_model,
                        messages=self.openai_messages,
                        tools=None,
                        temperature=self.temperature,
                        stream=True,
                    )

                    for chunk in followup_stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_content_chunks.append(delta.content)
                            if sentence_callback:
                                text_buffer += delta.content
                                s_clause, text_buffer = self._extract_next_speech_chunk(text_buffer)
                                while s_clause:
                                    clean_clause = self._clean_speech_clause(s_clause)
                                    if clean_clause:
                                        sentence_callback(clean_clause)
                                    s_clause, text_buffer = self._extract_next_speech_chunk(text_buffer)

                # Fallback for inline pseudo-XML tool calls (e.g. Qwen formatting)
                raw_content = "".join(full_content_chunks).strip()
                if not had_tool_call and "<tool_call>" in raw_content:
                    fn_match = re.search(r'<function=([a-zA-Z0-9_]+)>', raw_content)
                    if fn_match:
                        had_tool_call = True
                        fn_name = fn_match.group(1)
                        fn_args = {}
                        for p_match in re.finditer(r'<parameter=([a-zA-Z0-9_]+)>\s*(.*?)\s*</parameter>', raw_content, re.DOTALL):
                            fn_args[p_match.group(1)] = p_match.group(2).strip()

                        logger.info(f"{self.provider.upper()} Inline tool call: {fn_name}({fn_args})")
                        is_safe, warning = self.safety.check_tool_call(fn_name, fn_args)
                        tool_result = f"Action halted: {warning}" if not is_safe else SkillRegistry.execute(fn_name, **fn_args)
                        logger.info(f"{self.provider.upper()} Inline tool result: {tool_result}")

                        self.openai_messages.append({"role": "assistant", "content": raw_content})
                        self.openai_messages.append({
                            "role": "user",
                            "content": f"[System Tool Result: {tool_result}. Briefly acknowledge to Sir that this was completed.]"
                        })
                        followup_stream = self.openai_client.chat.completions.create(
                            model=self.openai_model,
                            messages=self.openai_messages,
                            tools=None,
                            temperature=self.temperature,
                            stream=True,
                        )
                        full_content_chunks = []
                        text_buffer = ""
                        for chunk in followup_stream:
                            if not chunk.choices:
                                continue
                            delta = chunk.choices[0].delta
                            if delta.content:
                                full_content_chunks.append(delta.content)
                                if sentence_callback:
                                    text_buffer += delta.content
                                    s_clause, text_buffer = self._extract_next_speech_chunk(text_buffer)
                                    while s_clause:
                                        clean_clause = self._clean_speech_clause(s_clause)
                                        if clean_clause:
                                            sentence_callback(clean_clause)
                                        s_clause, text_buffer = self._extract_next_speech_chunk(text_buffer)

                # Flush any remaining text in the buffer to TTS
                if sentence_callback and text_buffer.strip():
                    clean_clause = self._clean_speech_clause(text_buffer.strip())
                    if clean_clause:
                        sentence_callback(clean_clause)

                reply_text = "".join(full_content_chunks).strip() if full_content_chunks else "At your service, Sir."
                reply_text = re.sub(r"<tool_call>.*?</tool_call>", "", reply_text, flags=re.DOTALL).strip()
                if not reply_text:
                    reply_text = "Updated, Sir."

                self.openai_messages.append({"role": "assistant", "content": reply_text})
                self.memory.log_interaction(user_text, reply_text)
                if not had_tool_call:
                    self.memory.auto_learn_from_interaction(user_text, reply_text)

                # Bound memory window
                if len(self.openai_messages) > 25:
                    self.openai_messages = [self.openai_messages[0]] + self.openai_messages[-20:]

                return reply_text

            except Exception as e:
                last_error = e
                if model_candidate != candidate_models[-1]:
                    next_model = candidate_models[candidate_models.index(model_candidate) + 1]
                    logger.warning(
                        f"OmniRoute model '{model_candidate}' unavailable or quota limit reached ({e}). "
                        f"Seamlessly switching to next fallback model: '{next_model}'..."
                    )
                    continue
                else:
                    logger.warning(f"{self.provider.upper()} request failed on final candidate '{model_candidate}': {e}.")
                    break

        logger.warning(f"All candidate models exhausted on {self.provider.upper()} (last error: {last_error}). Seamlessly falling back to Gemini Flash...")
        return self._process_gemini_query(user_text, sentence_callback)

    def _process_gemini_query(self, user_text: str, sentence_callback: Any = None) -> str:
        """Processes user input using Google Gemini with dynamic candidate failover."""
        if not self.gemini_client:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if api_key:
                self.gemini_client = genai.Client(api_key=api_key)
                self._init_gemini()
            else:
                return "My apologies, Sir. My cognitive link requires an API key in the environment or .env file."

        attempts = len(self.gemini_candidates)
        for attempt in range(attempts):
            active_model = self.gemini_candidates[self.gemini_model_idx]
            if not self.gemini_chat or self.gemini_model != active_model:
                self._init_gemini(active_model)

            try:
                contents = [user_text]

                if self.should_capture_screen(user_text):
                    screenshot = ScreenPerception.capture_screen()
                    if screenshot:
                        contents.append(screenshot)

                response = self.gemini_chat.send_message(contents)

                gemini_tool_iterations = 0
                while response.function_calls and gemini_tool_iterations < 3:
                    gemini_tool_iterations += 1
                    tool_responses = []
                    for call in response.function_calls:
                        tool_name = call.name
                        tool_args = dict(call.args) if call.args else {}
                        logger.info(f"Tool call: {tool_name}({tool_args})")

                        is_safe, warning = self.safety.check_tool_call(tool_name, tool_args)
                        if not is_safe:
                            tool_result = f"Action halted: {warning}"
                        else:
                            tool_result = SkillRegistry.execute(tool_name, **tool_args)

                        logger.info(f"Tool result: {tool_result}")
                        tool_responses.append(types.Part.from_function_response(
                            name=tool_name,
                            response={"result": str(tool_result)}
                        ))

                    response = self.gemini_chat.send_message(tool_responses)

                reply_text = response.text.strip() if response.text else "At your service, Sir."
                self.memory.log_interaction(user_text, reply_text)
                self.memory.auto_learn_from_interaction(user_text, reply_text)

                if sentence_callback and reply_text:
                    sentences = [s.strip() for s in re.split(r"(?<!\d)(?<=[.!?])(?!\d)\s+", reply_text) if s.strip()]
                    for s in sentences:
                        sentence_callback(s)

                return reply_text

            except Exception as e:
                err_str = str(e)
                if ("503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str or "429" in err_str) and attempt < attempts - 1:
                    time.sleep(0.5)
                    next_idx = (self.gemini_model_idx + 1) % len(self.gemini_candidates)
                    next_model = self.gemini_candidates[next_idx]
                    logger.warning(f"Model '{active_model}' is busy (503/429). Seamlessly switching cognitive link to '{next_model}'...")
                    self.gemini_model_idx = next_idx
                    self.gemini_model = next_model
                    self._init_gemini(next_model)
                    continue

                logger.error(f"Error in Gemini processing: {e}")
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    return "My apologies, Sir. My neural uplink is currently experiencing high network congestion. Please give me a brief moment."
                return "I encountered a slight anomaly in my cognitive circuits, Sir. Please try again in a moment."

        return "My cognitive systems are temporarily congested, Sir. Please try again in a few moments."
