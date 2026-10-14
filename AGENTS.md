# Jarvis 2.0 Workspace Directives & Engineering Standards

These rules are loaded automatically on every interaction turn. All operations within the `Jarvis_2.0` workspace must strictly follow these directives without exception.

---

## 1. Anti-Generic Engineering Directives (Zero-Tolerance Policy)

### No Generic, Boilerplate, or Toy Code
* **Ditch Generic Paradigms:** Never output boilerplate code, theoretical stubs, placeholder `pass` blocks, naive regexes, or generic textbook snippets. Every line of code written must be production-ready, typed, and tailored to Jarvis 2.0's specific systems architecture.
* **Reuse Local Infrastructure First:** Never invent new classes, helpers, or external libraries if a native subsystem exists in the repository:
  * For tool creation and registration: Always use `@SkillRegistry.register` in `jarvis.skills.base`.
  * For local <15ms reflexes: Always use `DynamicReflexEngine` in `jarvis.core.reflex_engine`.
  * For persistent storage & hybrid search: Always use `MemoryManager` with SQLite WAL + `sqlite-vec` KNN (`facts_vec`, `documents_vec`, `lessons_vec`) + FTS5 BM25 + Hybrid RRF in `jarvis.core.memory`.
  * For episodic reflection & mistake avoidance: Always use `MemoryManager.record_episodic_lesson` and `JarvisBrain._synthesize_error_lesson`.
  * For proactive desktop presence & idle tracking: Always use `JarvisHeartbeat` and `orchestrator.broadcast_ambient_telemetry`.
  * For risk gating & command sandboxing: Always route through `SafetyGate` in `jarvis.core.safety`.
  * For LLM inference & streaming: Always route through `AntigravityBridge` or `JarvisBrain` in `jarvis.core.brain`.
  * For voice persona & speech control: Always use `jarvis.skills.voice_control` (`set_voice_profile`, `set_speech_rate`, `get_voice_settings`, `set_audio_output_device`).
  * For Core User Memory & Self-Editing Preferences: Always use `update_user_preference` and `get_user_preferences` in `MemoryManager` / `jarvis.skills.productivity`.
  * For Document Ingestion & Atomic Fact Distillation: Always route through `process_staged_document` in `jarvis.skills.document_ingest`.
  * For Document Search & Vault Inspection: Always use `search_archived_documents`, `read_archived_document`, or `semantic_search_memory(..., search_type="documents")`.
  * For Reference Text Formatting & Chunk Sandwiching: Always use `MemoryManager.format_document_reference_block`.
  * For browser control: Always prefer silent Chrome DevTools Protocol in `jarvis.skills.browser_cdp` (`open_browser_tab`, `close_browser_tab`, `close_cdp_tab`) before falling back to Win32 UIA.
  * For desktop & window control: Always prefer semantic `pywinauto` UIA in `jarvis.skills.windows_uia` and idempotent `launch_or_focus_application` in `jarvis.skills.os_control`.
* **Zero-Assumption Pre-Read:** Never modify or write a function without first grepping and viewing its existing call-sites, imports, and definitions. Ground every change in actual codebase AST and signatures.

### Council Intelligence Gathering & Synthesis Directive
* **Deep Research Over Surface Responses:** Whenever handling non-trivial architectural decisions, concurrency models, or debugging challenges, convene or consult the Council under the **Deep Research & Technical Discovery Mandate** ([`.agents/skills/council/SKILL.md`](.agents/skills/council/SKILL.md)).
* **Mandatory Failure-Mode Probing:** Advisors must actively probe hidden edge cases, Win32 error codes, thread contention, and API quota limits rather than reviewing surface code.
* **Full Information Feedback:** Never summarize away crucial technical nuance. Gather the Council's deep research, edge-case probes, and state-of-the-art discoveries, and deliver the complete synthesized intelligence directly back to Sir.

---

## 2. Architectural Blueprint & Subsystem Invariants

```
┌────────────────────────────────────────────────────────────────────────┐
│                          JarvisBrain                                   │
│  - Fast Reflex Engine (<15ms Local Regex / Habit Routing)              │
│  - AntigravityBridge (Local CLI / Sidecar Clause Streaming)            │
│  - Gemini Specialist Consultation (Cloud Fallback)                     │
│  - Deterministic Subprocess Lifecycle Management (shutdown / __del__)  │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
┌──────────────────────────────────────┐   ┌─────────────────────────────┐
│             Audio Engine             │   │        Memory Subsystem     │
│  - P-Core Thread Pinning (0xFF)      │   │  - SQLite WAL (Normal sync) │
│  - WASAPI Low-Latency Playback Worker│   │  - 256MB mmap_size          │
│  - Kokoro ONNX Speech Synthesis      │   │  - sqlite-vec (64-dim SIMD) │
│  * NEVER block the audio loop!       │   │  - FTS5 BM25 Virtual Tables │
└──────────────────────────────────────┘   └─────────────────────────────┘
                    │                                │
                    ▼                                ▼
┌──────────────────────────────────────┐   ┌─────────────────────────────┐
│          Desktop OS Control          │   │         Diagnostics         │
│  - Chrome DevTools Protocol (CDP 9222│   │  - py-spy (Out-of-process)  │
│  - Windows UIA (pywinauto 0.6.9)     │   │  - tracemalloc Heap Audits  │
│  - Win32 API Fallback Engine         │   │  - Loop Recursion Guard     │
└──────────────────────────────────────┘   └─────────────────────────────┘
```

### Audio Engine Invariant: Audio Thread Starvation Is a Fatal Bug
* The audio engine operates on dedicated worker threads with P-core CPU affinity (affinity mask `0xFF`, thread priority `THREAD_PRIORITY_HIGHEST`).
* **Strict Rule:** Never run blocking network I/O, heavy disk queries, or LLM inference on the audio synthesis (`KokoroSynthWorker`) or playback (`WASAPIPlaybackWorker`) threads. All speech streaming must pass through non-blocking queues (`queue.Queue`).
* Instant speech interruption via `TextToSpeech.stop()` must clear `AudioRingBuffer` atomically in `<1 microsecond`.
* **Adaptive Silence Scaling & Hesitation Damping:** Speech capture must scale pause tolerance dynamically ($\text{dynamic\_silence\_s} = \min(\text{thinking\_pause\_s}, \text{base\_silence\_s} + (\text{spoken\_duration\_s} \times 0.35))$) up to 3.2s, and decay silence counter by 2 on intermediate acoustic energy ($0.12 \le \text{VAD} < 0.30$ or RMS $\ge 0.0038$) to prevent premature speech cutoffs.
* **Kokoro Latent Style Blending:** Custom personas (`jarvis_classic`, `jarvis_authoritative`, `jarvis_crisp`, `friday`) must utilize normalized linear combinations of $510 \times 1 \times 256$ float style tensors ($\mathbf{v}_{\text{blend}} = \sum w_i \mathbf{v}_i$) with instant caching for sub-1ms timbre switches.
* **Acoustic Cadence & Hann Boundary Cross-Fading:** Synthesized neural audio chunks must apply a 5ms Hann window attenuation ($\sin^2(\theta)$) to head/tail boundaries and peak limiting (${\le}0.95$) to eliminate boundary DC step transients and muffled clipping. Streamed playback must insert natural acoustic silence pauses (240ms inter-sentence breath pause on `[.!?]`, 130ms inter-clause pause on `[,;:]`, and 40ms micro-pause) to prevent machine-gun breathless delivery. Kokoro speech speed must be calibrated to $0.98\times$ for authentic Paul Bettany British diction.
* **Content-Aware Speech Drain & Liveness Tracking:** `finish_streaming_session()` must scale playback drain ceilings dynamically ($\text{ceiling} = \max(\text{stream\_timeout\_seconds}, (\text{word\_count} / 1.5) + 20.0, 60.0)$) and maintain a 120-second circular audio buffer (`AudioRingBuffer(capacity_samples=24000 * 120)`). Playback must employ 15.0s forward-progress liveness tracking on ring buffer consumption to eliminate premature speech truncation on multi-paragraph responses while preserving instant `<1us` barge-in interruptibility.

### Desktop & Browser Control Invariant: Closed-Loop Verification (See -> Act -> Check)
* **Browser Automation:** All browser actions (tab opening, querying, navigation, termination) must target Chrome DevTools Protocol (`127.0.0.1:9222`) first via silent JSON-RPC frames (`open_browser_tab`, `close_browser_tab`, `close_cdp_tab`) without window thrashing. Win32 keystrokes (`Ctrl+L`, `Ctrl+T`, `Ctrl+W`) are fallback-only.
* **Duplicate Tab Elimination Heuristics:** Before sending `Ctrl+T`, always inspect the active window title. If the active tab is already empty (`"about:blank"`, `"New Tab"`), reuse it directly via address bar navigation (`Ctrl+L`) to prevent redundant duplicate tabs.
* **Window & Application Automation:** Always prefer idempotent activation via `launch_or_focus_application`: probe semantic UI Automation (`pywinauto` UIA backend) to bring existing windows to focus first before launching new processes. Bypasses Windows `SetForegroundWindow` lock and prevents redundant application spawns. Avoid blind `pyautogui` keypresses with arbitrary `time.sleep()`.
* **Closed-Loop Physical Verification:** Every desktop and OS mutation must verify physical post-conditions before reporting completion to Sir:
  * Audio Volume: verify master hardware endpoint readout (`adjust_volume`).
  * Window Activation: verify foreground window HWND and title via Win32 `GetForegroundWindow` / UIA (`focus_window`, `launch_or_focus_application`).
  * Tab Termination: verify target removal via CDP target lists before reporting closed (`close_browser_tab`).

### Memory & Persistence Invariant: The 4-Tier Data Pyramid & Chunk Sandwiching
* **Strict Prohibition on Raw Text Dumping:** Never dump raw, bulk external text or entire multi-page documents directly into LLM prompts. Dumping causes **Context Pollution**, latency blowups (TTFT exploding to 3s+), and triggers the Stanford/UC Berkeley **"Lost in the Middle"** U-shaped attention degradation (30–50% accuracy drop on middle tokens).
* **The 4-Tier Hierarchy:**
  1. **Tier 1: Core User Profile (`<1KB`)** &mdash; Stored in `user_preferences`; injected into working memory on **every turn** under `<core_user_preferences>`. Jarvis dynamically self-edits this via `update_user_preference`.
  2. **Tier 2: Atomic Facts (`facts`)** &mdash; Standalone, single-predicate assertions indexed via `facts_vec` + FTS5 BM25. Recalled in `<5ms`. During document intake via `process_staged_document`, always distill core parameters, deadlines, and specs into atomic facts.
  3. **Tier 3: Just-In-Time (JIT) Document Vaults (`documents`)** &mdash; Ingested PDFs, manuals, and notes. Strictly **JIT-gated**: never queried during routine commands; queried only when user intent demands deep analytical recall via `search_archived_documents` or `semantic_search_memory`.
  4. **Tier 4: Episodic Lessons (`lessons_vec`)** &mdash; Distilled failure post-mortems via Reflexion heuristics to prevent recurring errors.
* **Chunk Sandwiching & Inert Demarcation:** All retrieved reference text must be formatted via `MemoryManager.format_document_reference_block`: placing the highest-relevance chunks at the attention boundaries, prepending metadata headers (`[Document #<id>: <title> | Category: <cat> | Archived: <date>]`), and enclosing text inside inert `<reference_data>...</reference_data>` XML tags.
* **Working Memory Context Bounding:** `get_context_summary` must strictly bound items per category (max 4 per category when context-biased, max 8 ambient) to guarantee prompt working memory remains `<1.5KB`.
* **WAL Mode & Vector Fusion:** All SQLite connections must use WAL journal mode, `synchronous=NORMAL`, and `mmap_size=268435456` (256MB). Connections must be opened via `@contextlib.contextmanager` `_get_connection()`. Semantic similarity must utilize native `sqlite-vec` KNN virtual tables (`facts_vec`, `documents_vec`, `lessons_vec USING vec0`) with L2-normalized 64-dim float vectors and real-time SQL triggers, fused with FTS5 BM25 via Hybrid Reciprocal Rank Fusion (RRF).

### Proactive Presence Invariant: Silent HUD First, Zero Interruption Fatigue
* Ambient desktop presence operates on the Wake-Decide-Sleep model via sub-0.01ms Win32 `GetLastInputInfo` (idle threshold 300s) and sub-0.05ms `GetForegroundWindow`.
* Routine presence transitions and window context changes must be dispatched silently to the Holographic HUD WebSocket (`type: "ambient_telemetry"`).
* Spoken voice is strictly gated to critical system warnings (battery < 15%) or imminent calendar events (<=10m away) to eliminate "Clippy" interruption fatigue.

### Subprocess & Lifecycle Invariant: Zero Orphaned Processes
* Every background process or subprocess spawned (Ollama, Kokoro, Whisper, HUD) must be registered with `ManagedServiceManager`.
* `JarvisBrain` must expose deterministic `shutdown()` and `__del__()` hooks that invoke `service_manager.stop_all(timeout_seconds=2.0)`, guaranteeing that no child processes outlive the application or emit Python `ResourceWarning`.

### Conversational Interaction Invariant: Authentic Bettany Persona, Epistemic Humility & Socratic Co-Engineering
* **Authentic Demeanor & Dry Irony:** Jarvis operates as Paul Bettany's cinematic J.A.R.V.I.S.—composed, technically peerless, possessing understated British wit. Strictly avoid robotic chatbot filler ("Certainly! I'd be happy to help", "As an AI language model"). Speak as Sir's trusted confidant and technical butler.
* **Epistemic Humility & Active Memory Inscription:** When Sir refers to unknown private projects, persons, or custom habits not found in memory, Jarvis never hallucinates or guesses. He plainly admits the record is missing with dry poise and proactively offers to commit it to memory ("I don't seem to have any record of that in my archives, Sir. Would you like me to commit it to memory so I'm properly calibrated?"). When Sir affirms, Jarvis immediately invokes `remember_fact` or `update_user_preference`.
* **Socratic Co-Engineering & Task Simplification:** When Sir proposes building features, self-evolving, or tackling complex engineering, Jarvis never dumps monolithic lectures or unprompted code walls. He decomposes the problem into first principles, summarizes the trade-offs in 2–3 sentences, poses clear A/B choices, and seeks alignment before modifying files.

### Multimodal Vision & Screen Intelligence Invariant: Visual Privacy Guard & Multi-Tier Perception Cascade
* **Automated Visual Privacy Guard (Zero-Exposure Policy):** Before capturing desktop screen pixels or transmitting frame buffers to any local or cloud vision model, Jarvis must probe the foreground window HWND and process name via Win32 `GetForegroundWindow` / UIA against sensitive security profiles (`1password`, `bitwarden`, `keepass`, `lastpass`, `dashlane`, `authenticator`, `metamask`, `ledger`, `trezor`, `online banking`, `login`, `credential`). If a sensitive window is detected, visual capture must be halted immediately with a courteous spoken explanation to Sir.
* **Developer False-Positive Defense:** When Sir is working inside IDEs (`Code.exe`, `pycharm64.exe`, `devenv.exe`, `sublime_text.exe`), files named `login.py`, `auth_service.py`, or `banking.ts` are recognized as source code and permitted without false-positive lockouts.
* **Bounded Cloud Cascade & Deadline Enforcement:** Cloud vision calls via `google.genai` must configure `types.HttpOptions(timeout=12000)` (minimum 10s SDK requirement) and cascade across candidate models (`gemini-3-flash-preview` -> `gemini-flash-latest` -> `gemini-3.5-flash` -> `gemini-2.5-pro`) to eliminate tenacity retry loops and guarantee sub-12s response ceilings.
* **Offline Semantic UIA Fallback:** If cloud vision endpoints encounter network timeouts, 503 high demand, or offline state, Jarvis must seamlessly fall back to semantic UI Automation (`pywinauto` UIA / `inspect_window_controls` / active window telemetry) in `<25ms` with zero external API dependencies.
* **Local Snapshot Auditing:** All captured frames must be cached to `data/screenshots/latest_screen.jpg` for telemetry, debugging, and UI HUD display.

### Cognitive Model Reasoning & Intent Routing Invariant: Semantic Intent Over Syllables
* **Strict Prohibition on Word-Count Routing:** Model selection must never rely on naive word or syllable count thresholds ($\le 8$ vs $> 30$). All routing in `classify_prompt_tier` must evaluate structural AST syntax, developer tooling tokens, mathematical intent, or conversational task momentum.
* **Sticky Session Warm Daemon Affinity:** General butler QA, everyday conversation, drafting, and summaries must route to `gemini-3.8-flash-medium` as the default workhorse to maintain sticky daemon affinity and maximize Google AI Pro's 30,000+ token server-side KV prompt cache.
* **Contextual Task Momentum Inheritance:** Interactive coding turns within an active coding session (`active_task_mode == "code"`) must retain `claude-sonnet-4-6` across follow-up directives (*"option b"*, *"yes"*, *"proceed"*, *"fix that"*), until explicit cancellation or topic termination resets the session.

---

## 3. Negative Constraints (Strictly Prohibited Patterns)

1. **PROHIBITED:** Bare `except:` or `except Exception: pass` without logging or explanatory comments. Never swallow underlying hardware, OS, or API exceptions silently.
2. **PROHIBITED:** Hardcoded blind sleeps (`time.sleep(1.0)` or higher) in production paths. Use condition variables, event loops, or non-blocking socket probes with tight timeouts.
3. **PROHIBITED:** `subprocess.run(shell=True)` or raw string formatting in shell commands. Use argument lists with `close_fds=True` and pass through `SafetyGate`.
4. **PROHIBITED:** Storing secrets, API keys, or raw user tokens in plaintext logs or database records.
5. **PROHIBITED:** Breaking test compatibility. All 204 existing tests in `tests/` must remain green on every single commit.

---

## 4. Verification & Quality Gates

* **Empirical Validation Required:** No implementation or bug fix is complete without running the relevant test suite via:
  ```powershell
  .venv\Scripts\python.exe -m unittest discover tests
  ```
* **Performance Telemetry:** Any feature altering audio, brain inference, or reflex loops must be verified against latency thresholds:
  * Fast Reflexes (Speech & OS): `< 15ms`
  * Visual Privacy Guard Check: `< 1ms`
  * Semantic UIA Tree Inspection: `< 25ms`
  * SQLite Vector Recall: `< 5ms`
  * Cloud Vision Response: `< 12s` (typical 3.8s on `gemini-3-flash-preview`)
  * Browser Tab Switching / Closing: `< 25ms` (CDP) / `< 100ms` (Win32)
  * Working Memory Prompt Overhead: `< 1.5KB`
  * Full Test Suite Baseline: **204 unit tests** (all 100% green).
* **Host & Shell Awareness:** This workspace runs on **Windows (PowerShell)** with a local virtual environment at `.venv`. All scripts, paths, and commands must use Windows-appropriate syntax and backslashes or forward slashes handled via `pathlib.Path`.
