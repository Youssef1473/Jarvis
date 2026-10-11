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
        
        # Provider selection: "groq", "grok", or "gemini"
        self.provider = config.get("llm", {}).get("provider", "groq").lower()

        # OpenAI-compatible (Groq / xAI) state
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
        if self.provider in ("groq", "grok"):
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
        """Initializes client targeting Groq Cloud or xAI Grok."""
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        xai_key = os.getenv("XAI_API_KEY", "").strip() or os.getenv("GROK_API_KEY", "").strip()

        api_key = ""
        base_url = ""

        # Auto-detect Groq vs xAI based on provider configuration or key prefixes
        if self.provider == "groq" or (groq_key and groq_key.startswith("gsk_")):
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
            logger.info("No Groq or Grok API key found. Defaulting to Gemini.")
            self.provider = "gemini"
            return False

        try:
            from openai import OpenAI
            self.openai_client = OpenAI(
                api_key=api_key,
                base_url=base_url
            )
            self.openai_messages = [
                {"role": "system", "content": self._get_system_instruction()}
            ]
            logger.info(f"Jarvis {self.provider.upper()} link initialized with model: {self.openai_model}")
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

        self.gemini_chat = self.gemini_client.chats.create(
            model=self.gemini_model,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=self.temperature,
                tools=all_tools,
            )
        )
        logger.info(f"Jarvis Gemini session initialized with model: {self.gemini_model}")

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

        if self.provider in ("groq", "grok") and self.openai_client:
            return self._process_openai_compatible_query(user_text, sentence_callback)
        return self._process_gemini_query(user_text, sentence_callback)

    def _process_openai_compatible_query(self, user_text: str, sentence_callback: Any = None) -> str:
        """Processes user input using Groq or xAI Grok with function calling and screen vision."""
        logger.info(f"Routing query to {self.provider.upper()} ({self.openai_model})...")
        
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

        try:
            response = self.openai_client.chat.completions.create(
                model=self.openai_model,
                messages=self.openai_messages,
                tools=tools if tools else None,
                temperature=self.temperature,
            )
            msg = response.choices[0].message

            # Handle tool calls loop
            while msg.tool_calls:
                self.openai_messages.append(msg)
                for tool_call in msg.tool_calls:
                    fn_name = tool_call.function.name
                    try:
                        fn_args = json.loads(tool_call.function.arguments or "{}")
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
                        "tool_call_id": tool_call.id,
                        "content": str(tool_result),
                    })

                response = self.openai_client.chat.completions.create(
                    model=self.openai_model,
                    messages=self.openai_messages,
                    tools=tools if tools else None,
                    temperature=self.temperature,
                )
                msg = response.choices[0].message

            reply_text = msg.content.strip() if msg.content else "At your service, Sir."
            self.openai_messages.append({"role": "assistant", "content": reply_text})
            self.memory.log_interaction(user_text, reply_text)
            self.memory.auto_learn_from_interaction(user_text, reply_text)

            # Bound memory window
            if len(self.openai_messages) > 25:
                self.openai_messages = [self.openai_messages[0]] + self.openai_messages[-20:]

            # Dispatch sentences for pipelined streaming TTS
            if sentence_callback and reply_text:
                sentences = [s.strip() for s in re.split(r"(?<!\d)(?<=[.!?])(?!\d)\s+", reply_text) if s.strip()]
                for s in sentences:
                    sentence_callback(s)

            return reply_text

        except Exception as e:
            logger.warning(f"{self.provider.upper()} request failed: {e}. Seamlessly falling back to Gemini...")
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

                while response.function_calls:
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
