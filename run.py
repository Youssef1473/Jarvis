"""Main entry point for Jarvis 2.0."""
import os
import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.core.config_loader import load_config, get_gemini_api_key
from jarvis.core.orchestrator import JarvisOrchestrator
from jarvis.ui.tray import SystemTrayApp


def setup_logging():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silence noisy HTTP libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def check_api_key():
    key = get_gemini_api_key()
    if not key or key == "your_gemini_api_key_here":
        print("\n" + "!" * 60)
        print(" [ATTENTION] GEMINI_API_KEY is not configured yet!")
        print(" You can get a completely free key from Google AI Studio:")
        print(" 👉 https://aistudio.google.com/app/apikey")
        print("!" * 60)
        entered_key = input("Enter your Gemini API Key now (or press Enter to run offline): ").strip()
        if entered_key:
            env_file = Path(__file__).resolve().parent / ".env"
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(f"GEMINI_API_KEY={entered_key}\n")
            os.environ["GEMINI_API_KEY"] = entered_key
            print("API Key saved to .env!\n")


def main():
    setup_logging()
    check_api_key()

    config = load_config()
    orchestrator = JarvisOrchestrator(config)

    # Launch system tray icon in background
    tray_app = SystemTrayApp(on_quit_callback=orchestrator.stop)
    tray_app.run_in_background()

    # Run main interactive loop
    orchestrator.run_interactive_loop()


if __name__ == "__main__":
    main()
