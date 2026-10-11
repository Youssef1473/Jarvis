# Jarvis 2.0 — Personal AI Assistant

An Iron-Man inspired, voice-first personal AI assistant engineered for Windows 11 with an NVIDIA RTX GPU, featuring **$0 API costs**, multimodal desktop vision, push-to-talk, wake word detection, long-term memory, and tool automation.

---

## Features

- **Dual Voice Activation:**
  - **Push-to-Talk:** Press and hold `ALT` while speaking; release to immediately transcribe and process.
  - **Wake Word:** Say *"Hey Jarvis"* anytime to trigger voice command listening in the background.
  - **Barge-in / Interruption:** Start speaking or press any key to immediately cut off Jarvis's speech.
- **Cognitive Brain (Gemini 2.0 Flash):**
  - High-speed conversational reasoning and multi-step tool execution.
  - Multimodal desktop vision: Ask *"Jarvis, look at my screen"* or *"Explain this error"* to analyze your active desktop.
- **Hardware-Accelerated STT:** `faster-whisper` running locally on your NVIDIA GeForce RTX 3050 GPU using CUDA.
- **Procedural High-Tech Audio Cues:** Synthesized Stark Industries chimes for wake, thinking, done, and alerts.
- **Persistent Memory:** SQLite database (`data/jarvis_memory.db`) remembering your personal facts, preferences, habits, and tasks across sessions.
- **Safety Gate:** Voice confirmation required before executing potentially destructive PowerShell scripts or file operations.
- **Tool Suite:**
  - **Desktop & OS Automation:** Launch apps, adjust volume, control media playback, show desktop.
  - **Web & Live Knowledge:** DuckDuckGo web search, Open-Meteo real-time global weather, Wikipedia.
  - **Developer Shell:** PowerShell execution and directory/file inspection.
  - **Productivity:** Task list, reminders, timestamps, facts storage.
  - **MCP Extensibility:** Model Context Protocol bridge for connecting external community servers.
- **Windows System Tray:** Runs quietly in the background with an Arc-Reactor icon.

---

## Quick Start

### 1. Configure Gemini API Key (Free)
Copy `.env.example` to `.env` and add your free Google AI Studio key:
```bash
GEMINI_API_KEY=your_key_here
```
Get a free key here: [Google AI Studio](https://aistudio.google.com/app/apikey).

### 2. Launch Jarvis
Activate the virtual environment and run:
```powershell
.\.venv\Scripts\python.exe run.py
```

### 3. Usage
- **Hold ALT:** Speak your request, then release ALT.
- **Say "Hey Jarvis":** Jarvis wakes up, chimes, and listens.
- **Type in terminal:** Type your question directly in the `[Sir]:` prompt and hit Enter.

---

## Architecture & Customization

- Configuration settings: `config/config.yaml`
- Adding MCP servers: `config/mcp_servers.json`
- Adding new custom tools: add `@SkillRegistry.register` decorated functions in `jarvis/skills/`
- Running test suite:
  ```powershell
  .\.venv\Scripts\python.exe tests/test_skills.py
  .\.venv\Scripts\python.exe tests/test_memory.py
  .\.venv\Scripts\python.exe tests/test_sfx.py
  ```
