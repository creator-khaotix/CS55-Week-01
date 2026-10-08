"""Input side: microphone + voice activity detection, or a keyboard stand-in.

The mic listener runs VAD on every 30 ms frame inside the audio callback and
emits two kinds of events onto an asyncio queue:

* ``SpeechStarted`` as soon as speech is confirmed, which is what makes
  barge-in feel instant (playback stops before you finish your first word);
* ``Utterance`` with the full audio once you've been quiet for a moment.
"""

from __future__ import annotations

import asyncio
import collections
import sys
import threading
from typing import Callable

import numpy as np

from .events import InputEvent, Quit, SpeechStarted, Utterance

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


class MicListener:
    def __init__(
        self,
        queue: asyncio.Queue[InputEvent],
        *,
        vad_aggressiveness: int = 2,
        start_ms: int = 150,
        barge_in_ms: int = 350,
        end_silence_ms: int = 700,
        min_utterance_ms: int = 300,
        preroll_ms: int = 300,
        is_assistant_talking: Callable[[], bool] = lambda: False,
        device: int | str | None = None,
    ) -> None:
        import webrtcvad

        self._queue = queue
        self._loop = asyncio.get_running_loop()
        self._vad = webrtcvad.Vad(vad_aggressiveness)
        self._start_frames = max(1, start_ms // FRAME_MS)
        # While the assistant is talking its own voice can leak into the mic,
        # so demand a longer run of speech before treating it as a barge-in.
        self._barge_in_frames = max(1, barge_in_ms // FRAME_MS)
        self._end_frames = max(1, end_silence_ms // FRAME_MS)
        self._min_frames = max(1, min_utterance_ms // FRAME_MS)
        self._preroll: collections.deque[bytes] = collections.deque(
            maxlen=max(1, preroll_ms // FRAME_MS)
        )
        self._is_assistant_talking = is_assistant_talking
        self._device = device
        self._stream = None
        self.muted = False

        self._in_speech = False
        self._voiced_run = 0
        self._silent_run = 0
        self._frames: list[bytes] = []

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _emit(self, event: InputEvent) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def _on_audio(self, indata, frames, time_info, status) -> None:  # audio thread
        if self.muted:
            return
        frame = indata[:, 0].tobytes()
        if len(frame) != FRAME_SAMPLES * 2:
            return
        voiced = self._vad.is_speech(frame, SAMPLE_RATE)

        if not self._in_speech:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            needed = (
                self._barge_in_frames if self._is_assistant_talking() else self._start_frames
            )
            if self._voiced_run >= needed:
                self._in_speech = True
                self._silent_run = 0
                self._frames = list(self._preroll)
                self._preroll.clear()
                self._emit(SpeechStarted())
            return

        self._frames.append(frame)
        self._silent_run = 0 if voiced else self._silent_run + 1
        if self._silent_run >= self._end_frames:
            self._in_speech = False
            self._voiced_run = 0
            speech_frames = len(self._frames) - self._silent_run
            pcm = b"".join(self._frames)
            self._frames = []
            if speech_frames >= self._min_frames:
                audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                self._emit(Utterance(audio=audio))
            else:
                # Too short to be words (a cough, a click). An empty utterance
                # tells the conversation to resume anything it paused.
                self._emit(Utterance(text=""))


class KeyboardInput:
    """Type instead of talk. Each line counts as speech start + utterance.

    Useful for testing without a mic, and for when you're somewhere you can't
    talk. Typing while Claude is "speaking" interrupts it, just like voice.
    """

    def __init__(self, queue: asyncio.Queue[InputEvent]) -> None:
        self._queue = queue
        self._loop = asyncio.get_running_loop()

    def start(self) -> None:
        threading.Thread(target=self._read, daemon=True).start()

    def stop(self) -> None:
        pass

    def _read(self) -> None:
        for line in sys.stdin:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, SpeechStarted())
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, Utterance(text=line.rstrip("\n"))
            )
        self._loop.call_soon_threadsafe(self._queue.put_nowait, Quit())
