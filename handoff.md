# Agent Handoff: Jarvis 2.0 Engineering State & Architecture Runbook

**Generated At:** 2026-10-13T00:25:00+01:00  
**Target Agent:** Incoming Jarvis AI Pair Programmer / Autonomous Continuation Agent  
**Host Environment:** Windows 11 / WSL2 Arch Linux | Intel Core i5-12500H | NVIDIA GeForce RTX 3050 Laptop GPU (4GB VRAM) | 16GB System RAM  
**Python Runtime:** `.\.venv\Scripts\python.exe` (Python 3.12 virtualenv)  

---

## 🎯 1. Mission Objectives & Current Milestone Status

### Recently Completed & Verified Milestones:
1. **Self-Evolution AST Security, Semantic Canary & Rollback (Commit `b28b6a7`):**
   - Deep AST syntax & security analysis in `SkillSandbox.check_ast_security()` blocking dynamic reflection (`getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`), dunder attribute tampering (`__subclasses__`, `__globals__`, `__builtins__`, `__code__`, `__bases__`, `__mro__`), and prohibited imports (`ctypes`, `pickle`, `subprocess`, `pty`).
   - Automated Semantic Canary Test Generator (`generate_canary_script`) executing inside the isolated dry-run subprocess, checking module compile integrity, registration of tools via `@SkillRegistry.register`, and signature inspectability before hot-reload.
   - Transactional snapshot versioning in `data/evolution_snapshots/` with automatic rollback to previous functional versions if new updates fail canary verification or host hot-reloading.

2. **Multimodal Screen Perception & Fallback Cascade (Commit `98a2206`):**
   - Native `inspect_screen` and `see_screen` built-in skills in [`jarvis/skills/screen_vision.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/skills/screen_vision.py) utilizing DPI-aware `ScreenPerception.capture_screen_bytes()` and Google Gemini Multimodal Vision API cascade (`gemini-3.8-flash` -> `gemini-3.5-flash` -> `gemini-flash-latest`).
   - Graceful offline fallback to active OS window and process telemetry via `jarvis.skills.os_control.get_active_window()`.

3. **Sub-100ms Streaming Voice Reflex Pipeline (Commit `14fb6c3`):**
   - [`jarvis/audio/chunker.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/chunker.py): `StreamingSentenceChunker` providing speculative clause emission at earliest punctuation boundary (`,`, `.`, `!`, `?`, `;`, `:`, `\n`, `—`) with abbreviation/decimal/timestamp guards.
   - [`jarvis/audio/ring_buffer.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/ring_buffer.py): `AudioRingBuffer` pre-allocated circular PCM buffer with vectorized NumPy slice transfers for lock-free audio streaming and microsecond barge-in `clear()`.
   - [`jarvis/audio/tts.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/tts.py): Implemented `feed_token()`, `flush_tokens()`, and asynchronous low-latency hardware `sd.OutputStream` callback feeding directly from `AudioRingBuffer`.
   - [`jarvis/core/brain.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/brain.py): Updated `_extract_next_speech_chunk()` with speculative boundary sorting.

4. **Production WebSocket Telemetry & Metrics Engine (Commit `4e10fb8`):**
   - `TelemetryConnectionManager`: Dedicated single-slot `maxsize=1` lossy backpressure queues per subscriber dropping stale frames via `queue.get_nowait()`, 8-client ceiling, and single-pass minified JSON serialization (`c`, `rp`, `ru`, `rt`, `gp`, `gl`, `vu`, `vt`, `gt`, `st`, `md`, `m`, `ts`).
   - `WorkstationMetricsCollector`: Off-loop `psutil` and native `pynvml` metric sampling dispatched through `anyio.to_thread.run_sync`, protected with singleton `nvmlInit()` / `nvmlShutdown()` lifecycle management and WSL2 driver fallback gates.
   - Zero-CLS Viewport (`CLS = 0.000`): HUD indicator bars hardware-composited using `origin-left transform: scaleX(...)`, tabular numbers layout stability, pre-cached DOM element lookups, and clamped dimensions in [`jarvis/ui/templates/hud.html`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/ui/templates/hud.html).
   - False-Shutdown Bug Fixed: Patched `is_shutdown_command()` in [`jarvis/core/orchestrator.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/orchestrator.py) with code-marker guards and regex word boundaries.

---

## 📌 2. Repository & Branch State
- **Branch:** `main`
- **Latest Commit:** `b28b6a7` — `feat(evolution): implement deep AST security linting, semantic canary isolation, and transactional snapshot rollbacks`
- **Recent Commit Chain:**
  * `b28b6a7`: Deep AST security linting, semantic canary isolation, and transactional snapshot rollbacks.
  * `98a2206`: Multimodal screen perception and telemetry fallback cascade.
  * `14fb6c3`: Sub-100ms speculative streaming clause chunker and circular audio ring buffer for Kokoro TTS.
  * `4e10fb8`: Lossy WebSocket telemetry manager, NVML lifecycle hooks, Zero-CLS compositor HUD.
  * `28e1217`: Non-blocking 64KB photo streaming & ETag 304 caching (`/api/photos/{photo_id}`).
  * `e8bc96b`: Zero-VRAM Photo Archival Skill (`jarvis/core/skills/photo_manager.py`).
  * `dfe97e6`: Phase 1 structural overhaul (`UnifiedRunner`, `BaseProvider`, `SkillSandbox`).
- **Working Tree:** Clean (all tests green).

---

## ✅ 3. Completed & Verified Architectural Pillars (All Green)

| Subsystem | File Location | Key Architectural Invariants | Test Status |
| :--- | :--- | :--- | :--- |
| **Self-Evolution Canary & Security** | [`jarvis/core/evolution/sandbox.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/evolution/sandbox.py) | Deep AST linting blocking `getattr`/`__subclasses__`/`ctypes`, automated subprocess canary contract checks, transactional snapshot rollback. | `test_self_evolution_sandbox.py`<br>`test_self_evolution.py` (PASS) |
| **Screen Perception & Vision** | [`jarvis/skills/screen_vision.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/skills/screen_vision.py) | DPI-aware desktop capture (`ScreenPerception`), Gemini Multimodal Vision API cascade, OS active window telemetry fallback. | `test_screen_vision.py` (PASS) |
| **Streaming Voice Chunker** | [`jarvis/audio/chunker.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/chunker.py)<br>[`jarvis/audio/ring_buffer.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/ring_buffer.py)<br>[`jarvis/audio/tts.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/audio/tts.py) | Speculative token clause emission at first punctuation boundary, circular PCM slice transfers, sub-100ms TTFT audio delivery into Kokoro-82M. | `test_audio_streaming_pipeline.py`<br>`test_streaming_voice_reflex.py` (PASS) |
| **Unified Runner** | [`jarvis/core/providers/base.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/providers/base.py) | `BaseProvider(ABC)`, `UnifiedRunner` with 4-step ceiling, cycle detection halting infinite recursive loops, live tool narration. | `test_unified_runner_and_sandbox.py` (PASS) |
| **Zero-VRAM Photo Archival** | [`jarvis/core/skills/photo_manager.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/core/skills/photo_manager.py) | Pillow magic-byte validation, EXIF/geo scrubbing, UUID4+SHA-256 isolated paths in `data/photos/`, async SQLite queue worker, Gemini Cloud Vision offload. | `test_photo_manager.py` (PASS) |
| **FastAPI Photo Streamer** | [`jarvis/ui/server.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/ui/server.py) | Non-blocking 64KB chunk streaming (`StreamingResponse`), directory traversal defense, HTTP ETag 304 caching. | `test_hud.py` (PASS) |
| **Telemetry & NVML Engine** | [`jarvis/ui/server.py`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/ui/server.py) | Lossy single-slot `maxsize=1` backpressure drop-tail queues, 8-client ceiling, off-loop `psutil` + `pynvml` sampling via `anyio.to_thread.run_sync`, clean `shutdown_nvml()` hook. | `test_telemetry_connection_manager.py` (PASS) |
| **Zero-CLS Holographic HUD** | [`jarvis/ui/templates/hud.html`](file:///C:/Users/Youssef/Projects/Jarvis_2.0/jarvis/ui/templates/hud.html) | Strict `CLS = 0` layout budget, GPU compositor animations anchored with `origin-left transform: scaleX(...)`, tabular-nums mono typography, single-pass DOM reference cache. | `test_hud_zero_cls.py` (PASS) |

---

## 🧪 4. Current Verification Baseline

Run the complete test suite across the repository:
```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```
**Latest Output:**
```text
Ran 64 tests in 19.844s
OK
```

Targeted Evolution & Security Tests:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_self_evolution_sandbox.py tests/test_self_evolution.py tests/test_screen_vision.py
```
**Output:**
```text
Ran 17 tests in 1.100s
OK
```

---

## ⚠️ 5. Architectural Invariants & Non-Negotiables
1. **Zero Audio Stutter (Reflex Budget <15ms):** Kokoro-82M TTS and faster-whisper STT run on CUDA. Never block the event loop or introduce thread starvation during metric polling or image streaming.
2. **VRAM Ceiling:** Total local model allocation must never exceed **2.5 GB VRAM** (leaves 1.5GB for display, CUDA audio pipelines, and OS rendering on RTX 3050 4GB).
3. **Async Isolation:** Synchronous syscalls (`psutil`, `pynvml`, `os.path`, `open()`) are **banned** from Uvicorn's event loop. All telemetry queries must execute off-loop via `anyio.to_thread.run_sync`.
4. **Backpressure Strategy:** Discard old frames (`queue.get_nowait()` on `Queue(maxsize=1)`) rather than buffering backlogs in RAM.
5. **Zero Cumulative Layout Shift (`CLS = 0`):** Mutating `width`, `height`, or `margin` during telemetry updates is strictly forbidden. Animate gauges strictly via `transform: scaleX(...)` on `will-change-transform` layers.
6. **Destructive Action Safety:** Never delete files or modify database schemas without explicit confirmation.

---

## 🚀 6. Next Phase Architectural Horizon (Candidate Priorities)

### Priority 1: Local Hybrid Vision & Zero-Shot Triage (RTX 3050 Optimized)
- **Objective:** Ultra-fast local triage for desktop activity & screen captures before cloud offloading.
- **Approach:**
  - Lightweight local diff/perception gate to evaluate whether a screen change warrants high-tier Gemini Cloud Vision analysis.
  - Preserves cloud quota while keeping local GPU VRAM within 1.1GB (STT + TTS + Vision triage < 2.5GB ceiling).
- **Resource Budget Impact:** Capped at ~300MB VRAM or pure CPU perceptual hashing (pHash) off-thread.

### Priority 2: Persistent Tool Invocation Narration & Real-Time Voice Interruption (Barge-in)
- **Objective:** Enable instantaneous voice barge-in when Sir speaks while Jarvis is executing a multi-step tool call or speaking.
- **Approach:**
  - Hardware ring buffer `clear()` on VAD speech trigger.
  - Immediate `_stop_event.set()` signal draining the Kokoro synthesis worker in <1ms.
  - HUD visual feedback transitioning instantly to listening state.
