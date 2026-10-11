"""Test for Edge-TTS audio synthesis."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from jarvis.core.config_loader import load_config
from jarvis.audio.tts import TextToSpeech


async def test_tts():
    config = load_config()
    tts = TextToSpeech(config)
    print("Testing Edge-TTS synthesis...")
    audio_bytes = await tts._synthesize_audio("Jarvis systems operational, Sir.")
    assert len(audio_bytes) > 1000, f"Expected audio bytes, got {len(audio_bytes)}"
    print(f"Edge-TTS synthesized successfully! Total bytes: {len(audio_bytes)}")


if __name__ == "__main__":
    asyncio.run(test_tts())
