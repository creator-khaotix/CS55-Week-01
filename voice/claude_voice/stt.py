"""Speech-to-text backends. All run locally; audio never leaves the machine."""

from __future__ import annotations

import asyncio
import platform
import sys
from typing import Protocol

import numpy as np

# Whisper invents these on near-silent audio; never send them to Claude.
_HALLUCINATIONS = {
    "",
    "you",
    "thank you",
    "thank you.",
    "thanks for watching!",
    "thanks for watching.",
    "bye.",
    ".",
}


class STT(Protocol):
    async def transcribe(self, audio: np.ndarray) -> str: ...


def _clean(text: str) -> str:
    text = text.strip()
    return "" if text.lower() in _HALLUCINATIONS else text


class FasterWhisperSTT:
    """faster-whisper (CTranslate2). Good default on Linux/Intel; fine on Apple CPU."""

    def __init__(self, model: str = "small.en", vocabulary: str | None = None) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device="auto", compute_type="int8")
        self._prompt = vocabulary

    def _run(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=1,
            initial_prompt=self._prompt,
            condition_on_previous_text=False,
        )
        return "".join(s.text for s in segments)

    async def transcribe(self, audio: np.ndarray) -> str:
        return _clean(await asyncio.to_thread(self._run, audio))


class MLXWhisperSTT:
    """mlx-whisper: runs on the Apple Silicon GPU, the fastest option on a Mac."""

    def __init__(
        self, model: str = "mlx-community/whisper-small.en-mlx", vocabulary: str | None = None
    ) -> None:
        import mlx_whisper

        self._mlx = mlx_whisper
        self._model = model
        self._prompt = vocabulary

    def _run(self, audio: np.ndarray) -> str:
        result = self._mlx.transcribe(
            audio,
            path_or_hf_repo=self._model,
            language="en",
            initial_prompt=self._prompt,
            condition_on_previous_text=False,
        )
        return result["text"]

    async def transcribe(self, audio: np.ndarray) -> str:
        return _clean(await asyncio.to_thread(self._run, audio))


def default_stt_name() -> str:
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            import mlx_whisper  # noqa: F401

            return "mlx"
        except ImportError:
            pass
    return "faster-whisper"


def make_stt(name: str, model: str | None, vocabulary: str | None) -> STT:
    if name == "mlx":
        return MLXWhisperSTT(model or "mlx-community/whisper-small.en-mlx", vocabulary)
    if name == "faster-whisper":
        return FasterWhisperSTT(model or "small.en", vocabulary)
    raise ValueError(f"unknown STT backend: {name}")
