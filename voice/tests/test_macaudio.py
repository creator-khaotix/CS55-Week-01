import asyncio
import sys
import time
from pathlib import Path

from claude_voice.macaudio import EchoCancelledMic, HelperTTS, MacAudioHelper

FAKE = [sys.executable, str(Path(__file__).with_name("fake_audio_helper.py"))]


async def test_speak_waits_for_done_and_stop_returns_early():
    helper = MacAudioHelper(FAKE)
    helper.start()
    try:
        tts = HelperTTS(helper, voice=None, rate_wpm=None)
        t = time.monotonic()
        await tts.speak("hello there")
        assert 0.08 < time.monotonic() - t < 2

        # A long sentence that gets stopped returns right away.
        task = asyncio.create_task(tts.speak("x" * 400))
        await asyncio.sleep(0.02)
        tts.stop()
        await asyncio.wait_for(task, 1)
    finally:
        helper.close()


async def test_mic_frames_reach_the_vad():
    helper = MacAudioHelper(FAKE)
    helper.start()
    fed = []
    try:
        mic = EchoCancelledMic(asyncio.Queue(), helper)
        mic.segmenter.feed = fed.append
        mic.start()
        await asyncio.sleep(0.3)
        assert fed and all(len(f) == 960 for f in fed)
    finally:
        helper.close()
