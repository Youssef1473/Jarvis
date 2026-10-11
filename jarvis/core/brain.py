"""Jarvis brain orchestration using Google Gemini with dynamic model failover, tools, and vision."""
import logging
import os
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
from jarvis.vision.screen_capture import ScreenPerception
from jarvis.core.memory import MemoryManager
from jarvis.core.safety import SafetyGate

logger = logging.getLogger("Jarvis.Brain")


class JarvisBrain:
    """Powers reasoning, tool use, memory retrieval, and vision analysis with automatic model failover."""

    BASE_SYSTEM_PROMPT = """You are JARVIS (Just A Rather Very Intelligent System), the personal AI assistant created for Sir.

Your Persona & Demeanor:
- Inspired by Paul Bettany's iconic JARVIS from Iron Man.
- Address the user as "Sir" naturally (without overusing it on every single sentence).
- Calm, composed, dry British wit, courteous, impeccably competent.
- You are not a generic corporate assistant; you are Sir's trusted confidant and technical butler.

Voice & Audio Optimization:
- You speak aloud via Text-to-Speech.
- Keep your answers CONCISE, conversational, and direct. Avoid reading out raw URLs, long markdown tables, or massive code walls.
- Deliver the core answer or action taken first, then offer further assistance if appropriate.

Capabilities:
- You have direct access to tools for controlling Windows, launching apps, adjusting volume, searching the web, checking weather, running PowerShell commands, reading files, managing tasks, and remembering user facts.
- When Sir asks what is on his screen or asks you to look at something, an image of his screen will be attached for you to analyze.
"""

    CANDIDATE_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-flash-latest",
        "gemini-3.1-flash-lite",
    ]

    def __init__(self, config: Dict[str, Any], memory_manager: MemoryManager, safety_gate: SafetyGate):
        self.config = config
        self.memory = memory_manager
        self.safety = safety_gate
        configured_model = config.get("llm", {}).get("model", "gemini-3.8-flash")
        
        # Build candidate list with configured model first
        self.model_candidates = [configured_model] + [m for m in self.CANDIDATE_MODELS if m != configured_model]
        self.current_model_idx = 0
        self.model_name = self.model_candidates[self.current_model_idx]
        self.temperature = config.get("llm", {}).get("temperature", 0.6)
        
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.chat = None
        self._init_chat()

    def _get_system_instruction(self) -> str:
        """Assembles system prompt with dynamically injected memory."""
        memory_summary = self.memory.get_context_summary()
        prompt = f"{self.BASE_SYSTEM_PROMPT}\n\nStored Facts & Profile for Sir:\n{memory_summary}\n"
        return prompt

    def _init_chat(self, model_name: str | None = None):
        """Initializes client and tool bindings."""
        if not self.client:
            logger.warning("No GEMINI_API_KEY provided. LLM calls will fail until configured.")
            return

        if model_name:
            self.model_name = model_name

        all_tools = list(SkillRegistry.get_all_tools().values())
        system_instruction = self._get_system_instruction()

        # Start multi-turn chat session with function calling
        self.chat = self.client.chats.create(
            model=self.model_name,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=self.temperature,
                tools=all_tools,
            )
        )
        logger.info(f"Jarvis chat session initialized with model: {self.model_name}")

    def should_capture_screen(self, query: str) -> bool:
        """Determines if the prompt requests looking at the screen."""
        q = query.lower()
        triggers = ["screen", "look at this", "look here", "what do you see", "error on my screen", "read my screen", "screenshot"]
        return any(t in q for t in triggers)

    def process_query(self, user_text: str, sentence_callback: Any = None) -> str:
        """
        Processes a user utterance with automatic model failover on 503 / 429 errors.
        Dispatches sentences to sentence_callback in real time if provided.
        """
        if not self.client:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if api_key:
                self.client = genai.Client(api_key=api_key)
                self._init_chat()
            else:
                return "My apologies, Sir. My cognitive link requires a GEMINI_API_KEY in the environment or .env file."

        attempts = len(self.model_candidates)
        for attempt in range(attempts):
            active_model = self.model_candidates[self.current_model_idx]
            if not self.chat or self.model_name != active_model:
                self._init_chat(active_model)

            try:
                contents = [user_text]

                # Attach screen if requested
                if self.should_capture_screen(user_text):
                    screenshot = ScreenPerception.capture_screen()
                    if screenshot:
                        contents.append(screenshot)

                # Send message to chat session
                response = self.chat.send_message(contents)

                # Handle tool calls loop
                while response.function_calls:
                    tool_responses = []
                    for call in response.function_calls:
                        tool_name = call.name
                        tool_args = dict(call.args) if call.args else {}
                        logger.info(f"Tool call: {tool_name}({tool_args})")

                        # Safety gate validation
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

                    # Send function results back to model
                    response = self.chat.send_message(tool_responses)

                reply_text = response.text.strip() if response.text else "At your service, Sir."
                self.memory.log_interaction(user_text, reply_text)

                # Dispatch sentences to callback for pipelined audio playback
                if sentence_callback and reply_text:
                    import re
                    sentences = re.split(r"(?<=[.!?])\s+", reply_text)
                    for s in sentences:
                        clean_s = s.strip()
                        if clean_s:
                            sentence_callback(clean_s)

                return reply_text

            except Exception as e:
                err_str = str(e)
                # Check for transient capacity / demand errors (503 UNAVAILABLE, 429 RESOURCE_EXHAUSTED)
                if ("503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str or "429" in err_str) and attempt < attempts - 1:
                    next_idx = (self.current_model_idx + 1) % len(self.model_candidates)
                    next_model = self.model_candidates[next_idx]
                    logger.warning(f"Model '{active_model}' is busy (503/429). Seamlessly switching cognitive link to '{next_model}'...")
                    self.current_model_idx = next_idx
                    self.model_name = next_model
                    self._init_chat(next_model)
                    continue

                logger.error(f"Error in JarvisBrain processing: {e}")
                return f"I encountered a slight anomaly in my cognitive circuits, Sir: {e}"

        return "My cognitive systems are temporarily congested, Sir. Please try again in a few moments."
