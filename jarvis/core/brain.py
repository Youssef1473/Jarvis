"""Jarvis brain orchestration using Google Gemini 2.0 Flash with tools and vision."""
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
from jarvis.vision.screen_capture import ScreenPerception
from jarvis.core.memory import MemoryManager
from jarvis.core.safety import SafetyGate

logger = logging.getLogger("Jarvis.Brain")


class JarvisBrain:
    """Powers reasoning, tool use, memory retrieval, and vision analysis."""

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

    def __init__(self, config: Dict[str, Any], memory_manager: MemoryManager, safety_gate: SafetyGate):
        self.config = config
        self.memory = memory_manager
        self.safety = safety_gate
        self.model_name = config.get("llm", {}).get("model", "gemini-2.0-flash")
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

    def _init_chat(self):
        """Initializes client and tool bindings."""
        if not self.client:
            logger.warning("No GEMINI_API_KEY provided. LLM calls will fail until configured.")
            return

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
        logger.info("Jarvis chat session initialized with Gemini 2.0 Flash.")

    def should_capture_screen(self, query: str) -> bool:
        """Determines if the prompt requests looking at the screen."""
        q = query.lower()
        triggers = ["screen", "look at this", "look here", "what do you see", "error on my screen", "read my screen", "screenshot"]
        return any(t in q for t in triggers)

    def process_query(self, user_text: str) -> str:
        """
        Processes a user utterance, handles tool calls, attaches screen if needed,
        and returns the final text response.
        """
        if not self.client or not self.chat:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if api_key:
                self.client = genai.Client(api_key=api_key)
                self._init_chat()
            else:
                return "My apologies, Sir. My cognitive link requires a GEMINI_API_KEY in the environment or .env file."

        try:
            contents = [user_text]

            # If user asks about the screen, attach screen capture
            if self.should_capture_screen(user_text):
                screenshot = ScreenPerception.capture_screen()
                if screenshot:
                    contents.append(screenshot)

            # Send message to chat session
            response = self.chat.send_message(contents)

            # Check for function calls loop
            while response.function_calls:
                tool_responses = []
                for call in response.function_calls:
                    tool_name = call.name
                    tool_args = dict(call.args) if call.args else {}
                    logger.info(f"Tool call: {tool_name}({tool_args})")

                    # Check safety gate
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

                # Send function results back to Gemini
                response = self.chat.send_message(tool_responses)

            reply_text = response.text.strip() if response.text else "At your service, Sir."

            # Log interaction in SQLite
            self.memory.log_interaction(user_text, reply_text)
            return reply_text

        except Exception as e:
            logger.error(f"Error in JarvisBrain processing: {e}")
            return f"I encountered a slight anomaly in my cognitive circuits, Sir: {e}"
