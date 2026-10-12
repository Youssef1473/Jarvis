# Agent Handoff: Jarvis 2.0 Engineering State & Architecture Runbook

**Generated At:** 2026-10-13T00:48:00+01:00  
**Target Agent:** Incoming Jarvis AI Pair Programmer / Autonomous Continuation Agent  
**Host Environment:** Windows 11 / WSL2 Arch Linux | Intel Core i5-12500H | NVIDIA GeForce RTX 3050 Laptop GPU (4GB VRAM) | 16GB System RAM  
**Python Runtime:** `.\.venv\Scripts\python.exe` (Python 3.12 virtualenv)  

---

## 🎯 1. Mission Objectives & Current Milestone Status

### Recently Completed & Verified Milestones:
1. **Process Elevation, Async Tool Pool & Operational Guardrails (Commit `9e19f8e`):**
   - **Process Elevation & Performance Scheduling:** Elevated main process in [`run.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/run.py) to `ABOVE_NORMAL_PRIORITY_CLASS`, instructing Windows Thread Director to prioritize real-time audio threads on Intel Performance P-Cores.
   - **Real-Time WASAPI & Synthesis Thread Elevation:** Injected Win32 `SetThreadPriority(ctypes.c_void_p(-2), 2)` (`THREAD_PRIORITY_HIGHEST`) across Kokoro synthesis worker, playback worker, and sounddevice WASAPI callback in [`jarvis/audio/tts.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/tts.py).
   - **Async Thread-Pool Decoupling for Tools:** Refactored [`UnifiedRunner`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/providers/base.py) with dedicated 8-worker `ThreadPoolExecutor` (`Jarvis-ToolWorker`) offloading tools from the cognitive thread, and enforced a mandatory per-tool execution timeout (default 5.0s) via `execute_tool_with_timeout()` and `execute_tool_async()` preventing orchestrator stalls.
   - **Operational Sandbox Invariant Guards:** Upgraded [`SkillSandbox`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/evolution/sandbox.py) with static AST infinite loop detection (`while True:` without exit break/return), static allocation limits (>50M elements), and dynamic subprocess canary execution watchdog (2.0s wall-clock limit, global socket timeout 2.5s, `tracemalloc` memory ceiling at 64MB).
   - **Thread COM CoInitialize Protection:** Initialized COM on background tool worker threads in [`jarvis/skills/os_control.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/skills/os_control.py) before accessing Core Audio endpoints (`pycaw`).

2. **Self-Evolution AST Security, Semantic Canary & Rollback (Commit `b28b6a7`):**
   - Deep AST syntax & security analysis in `SkillSandbox.check_ast_security()` blocking dynamic reflection (`getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`), dunder attribute tampering (`__subclasses__`, `__globals__`, `__builtins__`, `__code__`, `__bases__`, `__mro__`), and prohibited imports (`ctypes`, `pickle`, `subprocess`, `pty`).
   - Automated Semantic Canary Test Generator (`generate_canary_script`) executing inside the isolated dry-run subprocess, checking module compile integrity, registration of tools via `@SkillRegistry.register`, and signature inspectability before hot-reload.
   - Transactional snapshot versioning in `data/evolution_snapshots/` with automatic rollback to previous functional versions if new updates fail canary verification or host hot-reloading.

3. **Multimodal Screen Perception & Fallback Cascade (Commit `98a2206`):**
   - Native `inspect_screen` and `see_screen` built-in skills in [`jarvis/skills/screen_vision.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/skills/screen_vision.py) utilizing DPI-aware `ScreenPerception.capture_screen_bytes()` and Google Gemini Multimodal Vision API cascade (`gemini-3.8-flash` -> `gemini-3.5-flash` -> `gemini-flash-latest`).
   - Graceful offline fallback to active OS window and process telemetry via `jarvis.skills.os_control.get_active_window()`.

4. **Sub-100ms Streaming Voice Reflex Pipeline (Commit `14fb6c3`):**
   - [`jarvis/audio/chunker.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/chunker.py): `StreamingSentenceChunker` providing speculative clause emission at earliest punctuation boundary (`,`, `.`, `!`, `?`, `;`, `:`, `\n`, `—`) with abbreviation/decimal/timestamp guards.
   - [`jarvis/audio/ring_buffer.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/ring_buffer.py): `AudioRingBuffer` pre-allocated circular PCM buffer with vectorized NumPy slice transfers for lock-free audio streaming and microsecond barge-in `clear()`.
   - [`jarvis/audio/tts.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/tts.py): Implemented `feed_token()`, `flush_tokens()`, and asynchronous low-latency hardware `sd.OutputStream` callback feeding directly from `AudioRingBuffer`.

---

## 📌 2. Repository & Branch State
- **Branch:** `main`
- **Latest Commit:** `9e19f8e` — `feat(core): elevate process scheduling to ABOVE_NORMAL, decouple tool execution into async worker pool, and enforce runtime sandbox operational guardrails`
- **Recent Commit Chain:**
  * `9e19f8e`: Process elevation, async tool worker pool, runtime sandbox operational guardrails.
  * `d1bfb8a`: Update agent handoff with verified milestones and 64-test baseline.
  * `b28b6a7`: Deep AST security linting, semantic canary isolation, and transactional snapshot rollbacks.
  * `98a2206`: Multimodal screen perception and telemetry fallback cascade.
  * `14fb6c3`: Sub-100ms speculative streaming clause chunker and circular audio ring buffer for Kokoro TTS.
  * `4e10fb8`: Lossy WebSocket telemetry manager, NVML lifecycle hooks, Zero-CLS compositor HUD.
- **Working Tree:** Clean (all tests green).

---

## ✅ 3. Completed & Verified Architectural Pillars (All Green)

| Subsystem | File Location | Key Architectural Invariants | Test Status |
| :--- | :--- | :--- | :--- |
| **Async Tool Execution Pool** | [`jarvis/core/providers/base.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/providers/base.py) | Non-blocking `Jarvis-ToolWorker` thread pool, mandatory 5.0s per-tool timeout guard, cycle detection, zero orchestrator lockup. | `test_unified_runner_and_sandbox.py` (PASS) |
| **Operational Sandbox Guards** | [`jarvis/core/evolution/sandbox.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/evolution/sandbox.py) | AST infinite loop detection, 50M element allocation ceiling, 2.0s canary smoke watchdog, 64MB memory limit, 2.5s socket timeout. | `test_self_evolution_sandbox.py`<br>`test_self_evolution.py` (PASS) |
| **P-Core Priority Scheduling** | [`run.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/run.py)<br>[`jarvis/audio/tts.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/tts.py) | Process set to `ABOVE_NORMAL`, Kokoro synth + WASAPI threads set to `THREAD_PRIORITY_HIGHEST` for P-Core execution. | Full Suite (PASS) |
| **Screen Perception & Vision** | [`jarvis/skills/screen_vision.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/skills/screen_vision.py) | DPI-aware desktop capture (`ScreenPerception`), Gemini Multimodal Vision API cascade, OS active window telemetry fallback. | `test_screen_vision.py` (PASS) |
| **Streaming Voice Chunker** | [`jarvis/audio/chunker.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/chunker.py)<br>[`jarvis/audio/ring_buffer.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/ring_buffer.py) | Speculative token clause emission at first punctuation boundary, circular PCM slice transfers, sub-100ms TTFT audio delivery into Kokoro-82M. | `test_audio_streaming_pipeline.py`<br>`test_streaming_voice_reflex.py` (PASS) |
| **Zero-VRAM Photo Archival** | [`jarvis/core/skills/photo_manager.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/skills/photo_manager.py) | Pillow magic-byte validation, EXIF/geo scrubbing, UUID4+SHA-256 isolated paths in `data/photos/`, async SQLite queue worker, Gemini Cloud Vision offload. | `test_photo_manager.py` (PASS) |
| **Telemetry & NVML Engine** | [`jarvis/ui/server.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/ui/server.py) | Lossy single-slot `maxsize=1` backpressure drop-tail queues, 8-client ceiling, off-loop `psutil` + `pynvml` sampling via `anyio.to_thread.run_sync`. | `test_telemetry_connection_manager.py` (PASS) |
| **Zero-CLS Holographic HUD** | [`jarvis/ui/templates/hud.html`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/ui/templates/hud.html) | Strict `CLS = 0` layout budget, GPU compositor animations anchored with `origin-left transform: scaleX(...)`, tabular-nums mono typography. | `test_hud_zero_cls.py` (PASS) |

---

## 🧪 4. Current Verification Baseline

Run the complete test suite across the repository:
```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```
**Latest Output:**
```text
Ran 68 tests in 21.607s
OK
```

Targeted Core, Concurrency, and Sandbox Tests:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_unified_runner_and_sandbox.py tests/test_self_evolution_sandbox.py tests/test_audio_streaming_pipeline.py
```
**Output:**
```text
Ran 20 tests in 2.850s
OK
```

---

## ⚠️ 5. Architectural Invariants & Non-Negotiables
1. **Zero Audio Stutter (Reflex Budget <15ms):** Kokoro-82M TTS and faster-whisper STT run on CUDA. Real-time audio threads stay elevated at `THREAD_PRIORITY_HIGHEST` on Performance P-cores.
2. **VRAM Ceiling:** Total local model allocation must never exceed **2.5 GB VRAM** (leaves 1.5GB for display, CUDA audio pipelines, and OS rendering on RTX 3050 4GB).
3. **Async Tool Isolation:** All tools execute offloaded through `UnifiedRunner`'s `ThreadPoolExecutor` with a mandatory 5.0s timeout. Never execute blocking tool calls synchronously on the cognitive orchestrator thread.
4. **Backpressure Strategy:** Discard old frames (`queue.get_nowait()` on `Queue(maxsize=1)`) rather than buffering backlogs in RAM.
5. **Zero Cumulative Layout Shift (`CLS = 0`):** Animate HUD gauges strictly via `transform: scaleX(...)` on `will-change-transform` layers.
6. **Destructive Action Safety:** Never delete files or modify database schemas without explicit confirmation.
