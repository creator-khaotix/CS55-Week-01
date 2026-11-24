"""Echo-cancelled audio on macOS through the `claude-voice-audio` helper.

The helper (macos/AudioHelper.swift) owns both the mic and the speaker so
Apple's voice processing can subtract Claude's voice from what the mic hears.
That's what makes barge-in work on laptop speakers without headphones.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

from .audio import FRAME_SAMPLES, VadSegmenter
from .events import InputEvent

HELPER_PATH = Path(__file__).resolve().parent.parent / "macos" / "claude-voice-audio"


def helper_available() -> bool:
    return sys.platform == "darwin" and HELPER_PATH.exists()


class MacAudioHelper:
    def __init__(self, argv: list[str] | None = None) -> None:
        self._argv = argv or [str(HELPER_PATH)]
        self._loop = asyncio.get_running_loop()
        self._ready = threading.Event()
        self._error: str | None = None
        self._done: dict[int, asyncio.Future[None]] = {}
        self._next_id = 0
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 10.0) -> None:
        self.proc = subprocess.Popen(
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        threading.Thread(target=self._read_events, daemon=True).start()
        if not self._ready.wait(timeout) or self._error:
            self.close()
            raise RuntimeError(f"audio helper failed to start: {self._error or 'timed out'}")

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()

    def send(self, command: dict) -> None:
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write((json.dumps(command) + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def _read_events(self) -> None:  # helper's stderr, on a thread
        assert self.proc and self.proc.stderr
        for raw in self.proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if not line.startswith("EVT "):
                print(f"[audio helper] {line}", file=sys.stderr)
                continue
            kind, _, rest = line[4:].partition(" ")
            if kind == "ready":
                self._ready.set()
            elif kind == "error":
                self._error = rest
                self._ready.set()
                print(f"[audio helper] error: {rest}", file=sys.stderr)
            elif kind == "done" and rest.isdigit():
                self._loop.call_soon_threadsafe(self._resolve, int(rest))
        self._ready.set()  # helper exited

    def _resolve(self, speech_id: int) -> None:
        fut = self._done.pop(speech_id, None)
        if fut and not fut.done():
            fut.set_result(None)

    # -- speech -------------------------------------------------------------

    def new_speech(self) -> tuple[int, asyncio.Future[None]]:
        self._next_id += 1
        fut = self._loop.create_future()
        self._done[self._next_id] = fut
        return self._next_id, fut

    def cancel_all(self) -> None:
        for fut in self._done.values():
            if not fut.done():
                fut.set_result(None)
        self._done.clear()


class HelperTTS:
    """TTS backend that speaks through the helper, so it gets echo-cancelled."""

    def __init__(self, helper: MacAudioHelper, voice: str | None, rate_wpm: int | None) -> None:
        self._helper = helper
        self._voice = voice
        # `say` speaks ~180 words per minute by default; the helper takes a multiplier.
        self._rate = rate_wpm / 180 if rate_wpm else None

    async def speak(self, text: str) -> None:
        speech_id, done = self._helper.new_speech()
        self._helper.send(
            {"cmd": "speak", "id": speech_id, "text": text, "voice": self._voice, "rate": self._rate}
        )
        try:
            # Safety net in case the helper never reports back.
            await asyncio.wait_for(done, timeout=10 + len(text) / 8)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._helper.send({"cmd": "stop"})
        self._helper.cancel_all()


class EchoCancelledMic:
    """Mic input from the helper's stdout, fed through the same VAD as MicListener."""

    def __init__(self, queue: asyncio.Queue[InputEvent], helper: MacAudioHelper, **vad) -> None:
        self.segmenter = VadSegmenter(queue, **vad)
        self._helper = helper

    def start(self) -> None:
        threading.Thread(target=self._read, daemon=True).start()

    def stop(self) -> None:
        pass

    def _read(self) -> None:
        assert self._helper.proc and self._helper.proc.stdout
        stdout = self._helper.proc.stdout
        frame_bytes = FRAME_SAMPLES * 2
        buf = b""
        while chunk := stdout.read1(frame_bytes):
            buf += chunk
            while len(buf) >= frame_bytes:
                self.segmenter.feed(buf[:frame_bytes])
                buf = buf[frame_bytes:]
