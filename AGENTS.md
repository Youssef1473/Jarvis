# Jarvis 2.0 Workspace Directives & Engineering Standards

These rules are loaded automatically on every interaction turn. All operations within the `Jarvis_2.0` workspace must strictly follow these directives without exception.

---

## 1. Anti-Generic Engineering Directives (Zero-Tolerance Policy)

### No Generic, Boilerplate, or Toy Code
* **Ditch Generic Paradigms:** Never output boilerplate code, theoretical stubs, placeholder `pass` blocks, naive regexes, or generic textbook snippets. Every line of code written must be production-ready, typed, and tailored to Jarvis 2.0's specific systems architecture.
* **Reuse Local Infrastructure First:** Never invent new classes, helpers, or external libraries if a native subsystem exists in the repository:
  * For tool creation and registration: Always use `@SkillRegistry.register` in `jarvis.skills.base`.
  * For local <15ms reflexes: Always use `DynamicReflexEngine` in `jarvis.core.reflex_engine`.
  * For persistent storage or search: Always use `MemoryManager` with SQLite WAL + `sqlite-vec` KNN + FTS5 BM25 in `jarvis.core.memory`.
  * For risk gating & command sandboxing: Always route through `SafetyGate` in `jarvis.core.safety`.
  * For LLM inference & streaming: Always route through `AntigravityBridge` or `JarvisBrain` in `jarvis.core.brain`.
  * For browser control: Always prefer silent Chrome DevTools Protocol in `jarvis.skills.browser_cdp` before falling back to Win32 UIA.
  * For desktop & window control: Always prefer semantic `pywinauto` UIA in `jarvis.skills.windows_uia`.
* **Zero-Assumption Pre-Read:** Never modify or write a function without first grepping and viewing its existing call-sites, imports, and definitions. Ground every change in actual codebase AST and signatures.

---

## 2. Architectural Blueprint & Subsystem Invariants

```
┌────────────────────────────────────────────────────────────────────────┐
│                          JarvisBrain                                   │
│  - Fast Reflex Engine (<15ms Local Regex / Habit Routing)              │
│  - AntigravityBridge (Local CLI / Sidecar Clause Streaming)            │
│  - Gemini Specialist Consultation (Cloud Fallback)                     │
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

### Desktop & Browser Control Invariant: Zero Focus Stealing
* **Browser Automation:** All browser actions (tab opening, querying, navigation) must target Chrome DevTools Protocol (`127.0.0.1:9222`) first via silent JSON-RPC frames without window thrashing. Win32 keystrokes (`Ctrl+L`, `Ctrl+T`) are fallback-only.
* **Window Automation:** Use semantic UI Automation (`pywinauto` UIA backend) to focus and inspect windows. Avoid blind `pyautogui` keypresses with arbitrary `time.sleep()`.

### Memory & Persistence Invariant: WAL Mode + sqlite-vec
* All SQLite connections must use WAL journal mode, `synchronous=NORMAL`, and `mmap_size=268435456` (256MB).
* Connections must be opened via the `@contextlib.contextmanager` `_get_connection()` pattern and closed deterministically.
* Semantic similarity must utilize native `sqlite-vec` KNN virtual tables (`facts_vec USING vec0`) with L2-normalized 64-dim float vectors.

---

## 3. Negative Constraints (Strictly Prohibited Patterns)

1. **PROHIBITED:** Bare `except:` or `except Exception: pass` without logging or explanatory comments. Never swallow underlying hardware, OS, or API exceptions silently.
2. **PROHIBITED:** Hardcoded blind sleeps (`time.sleep(1.0)` or higher) in production paths. Use condition variables, event loops, or non-blocking socket probes with tight timeouts.
3. **PROHIBITED:** `subprocess.run(shell=True)` or raw string formatting in shell commands. Use argument lists with `close_fds=True` and pass through `SafetyGate`.
4. **PROHIBITED:** Storing secrets, API keys, or raw user tokens in plaintext logs or database records.
5. **PROHIBITED:** Breaking test compatibility. All 128 existing tests in `tests/` must remain green on every single commit.

---

## 4. Verification & Quality Gates

* **Empirical Validation Required:** No implementation or bug fix is complete without running the relevant test suite via:
  ```powershell
  .venv\Scripts\python.exe -m unittest discover tests
  ```
* **Performance Telemetry:** Any feature altering audio, brain inference, or reflex loops must be verified against latency thresholds:
  * Fast Reflexes: `< 15ms`
  * SQLite Vector Recall: `< 5ms`
  * Browser Tab Switching: `< 25ms` (CDP) / `< 100ms` (Win32)
* **Host & Shell Awareness:** This workspace runs on **Windows (PowerShell)** with a local virtual environment at `.venv`. All scripts, paths, and commands must use Windows-appropriate syntax and backslashes or forward slashes handled via `pathlib.Path`.
