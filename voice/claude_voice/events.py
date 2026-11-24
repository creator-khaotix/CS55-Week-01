"""Events flowing from the input side (mic or keyboard) to the conversation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SpeechStarted:
    """The user started talking. Fired early so we can stop talking over them."""


@dataclass
class Utterance:
    """The user finished a phrase. Exactly one of `audio` / `text` is set."""

    audio: np.ndarray | None = None  # float32 mono at 16 kHz
    text: str | None = None


@dataclass
class Quit:
    pass


InputEvent = SpeechStarted | Utterance | Quit
