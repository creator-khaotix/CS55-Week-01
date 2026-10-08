"""Text-to-speech backends and the interruptible `Speaker` queue."""

from __future__ import annotations

import asyncio
import collections
import shutil
import sys
from typing import Callable, Protocol


class TTSBackend(Protocol):
    async def speak(self, text: str) -> None:
        """Say `text`; return when done or when `stop()` is called."""

    def stop(self) -> None:
        """Cut off whatever is playing, immediately."""


class SubprocessTTS:
    """Any CLI that speaks its argument: macOS `say`, `espeak-ng`, ...

    Stopping kills the process, which silences it instantly.
    """

    def __init__(self, argv: Callable[[str], list[str]]) -> None:
        self._argv = argv
        self._proc: asyncio.subprocess.Process | None = None

    async def speak(self, text: str) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv(text),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await self._proc.wait()
        finally:
            self._proc = None

    def stop(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()


def say_tts(voice: str | None = None, rate: int | None = None) -> SubprocessTTS:
    def argv(text: str) -> list[str]:
        cmd = ["say"]
        if voice:
            cmd += ["-v", voice]
        if rate:
            cmd += ["-r", str(rate)]
        return cmd + ["--", text]

    return SubprocessTTS(argv)


def espeak_tts(voice: str | None = None, rate: int | None = None) -> SubprocessTTS:
    exe = shutil.which("espeak-ng") or "espeak"

    def argv(text: str) -> list[str]:
        cmd = [exe]
        if voice:
            cmd += ["-v", voice]
        if rate:
            cmd += ["-s", str(rate)]
        return cmd + ["--", text]

    return SubprocessTTS(argv)


class KokoroTTS:
    """Kokoro-82M: a small local neural voice that sounds far better than `say`."""

    SAMPLE_RATE = 24_000

    def __init__(self, voice: str | None = None, speed: float = 1.1) -> None:
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code="a")
        self._voice = voice or "af_heart"
        self._speed = speed
        self._stopped = False

    def _synth(self, text: str):
        import numpy as np

        chunks = [
            audio
            for _, _, audio in self._pipeline(text, voice=self._voice, speed=self._speed)
        ]
        return np.concatenate(chunks) if chunks else None

    async def speak(self, text: str) -> None:
        import sounddevice as sd

        self._stopped = False
        audio = await asyncio.to_thread(self._synth, text)
        if audio is None or self._stopped:
            return
        sd.play(audio, self.SAMPLE_RATE)
        await asyncio.to_thread(sd.wait)

    def stop(self) -> None:
        import sounddevice as sd

        self._stopped = True
        sd.stop()


class PrintTTS:
    """No audio: print what would be said, taking roughly as long as speech."""

    def __init__(self, chars_per_second: float = 15.0) -> None:
        self._cps = chars_per_second
        self._stop = asyncio.Event()

    async def speak(self, text: str) -> None:
        self._stop.clear()
        print(f"  \N{SPEAKER WITH THREE SOUND WAVES} {text}", flush=True)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=len(text) / self._cps)
            print("  \N{SPEAKER WITH CANCELLATION STROKE} (cut off)", flush=True)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()


def default_tts_name() -> str:
    return "say" if sys.platform == "darwin" else "espeak"


def make_tts(name: str, voice: str | None, rate: int | None) -> TTSBackend:
    if name == "say":
        return say_tts(voice, rate)
    if name == "espeak":
        return espeak_tts(voice, rate)
    if name == "kokoro":
        return KokoroTTS(voice)
    if name == "print":
        return PrintTTS()
    raise ValueError(f"unknown TTS backend: {name}")


class Speaker:
    """A queue of sentences in front of a TTS backend, built for barge-in.

    * `say()` queues a sentence; playback starts as soon as one is queued.
    * `hold()` is what happens when the user starts talking: playback stops
      now, but the queue is kept and the interrupted sentence is remembered.
    * `resume()` picks up again (from the start of the interrupted sentence)
      if it turned out to be a cough or an "mm-hm".
    * `clear()` drops everything, for when the user said something real.
    """

    def __init__(self, backend: TTSBackend) -> None:
        self._backend = backend
        self._queue: collections.deque[str] = collections.deque()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._held = False
        self._current: str | None = None
        self._cut_off: str | None = None
        self._interrupted = False
        self.last_spoken: str | None = None  # last sentence played to the end

    @property
    def busy(self) -> bool:
        """True while speaking or with sentences waiting (held or not)."""
        return self._current is not None or bool(self._queue) or self._cut_off is not None

    @property
    def talking(self) -> bool:
        return self._current is not None

    def say(self, text: str) -> None:
        self._queue.append(text)
        self._idle.clear()
        self._wake.set()

    def hold(self) -> None:
        self._held = True
        if self._current is not None:
            self._cut_off = self._current
            self._interrupted = True
            self._backend.stop()

    def resume(self) -> None:
        if self._cut_off is not None:
            self._queue.appendleft(self._cut_off)
            self._cut_off = None
        self._held = False
        self._wake.set()

    def clear(self) -> None:
        self._queue.clear()
        self._cut_off = None
        self._held = False
        if self._current is not None:
            self._interrupted = True
            self._backend.stop()
        self._update_idle()

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def _update_idle(self) -> None:
        if not self.busy:
            self._idle.set()

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._queue and not self._held:
                text = self._queue.popleft()
                self._current = text
                self._interrupted = False
                try:
                    await self._backend.speak(text)
                finally:
                    self._current = None
                if not self._interrupted:
                    self.last_spoken = text
            self._update_idle()
