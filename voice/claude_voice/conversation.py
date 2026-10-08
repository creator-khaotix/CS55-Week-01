"""The turn-taking loop: who is talking, and what happens when you cut in.

Barge-in works in two stages, like on a phone call:

1. The moment the mic hears you (``SpeechStarted``), Claude's voice stops. The
   unspoken sentences are kept, because it might just be a cough or "mm-hm".
2. When you finish (``Utterance``), we decide what it was:
   * nothing / a backchannel ("uh-huh", "go on")  -> resume speaking;
   * "stop", "never mind", "shut up"...           -> interrupt the agent, silence;
   * the answer to a pending "Okay to run X?"     -> approve / deny the tool;
   * anything else                                -> interrupt the agent if it is
     still working, and send what you said as the next message, with a note on
     what you'd actually heard before cutting in.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from .agent import AgentEvent, TextBlockEnd, TextDelta, ToolStarted, TurnDone, VoicePermissionGate
from .events import InputEvent, Quit, SpeechStarted, Utterance
from .speech_text import SpeechChunker
from .stt import STT
from .tts import Speaker
from .ui import Console

_BACKCHANNEL = re.compile(
    r"^(uh[- ]?huh|mm[- ]?hm+|m+|hm+|yeah|yes|yep|ok(ay)?|right|sure|got it|go on|"
    r"keep going|continue|carry on|i see|cool|nice|uh|um|ah|oh)$"
)
_STOP = re.compile(
    r"^(stop|stop it|stop talking|be quiet|quiet|shut up|shush|never ?mind|cancel|"
    r"forget it|that's enough|enough|hold on|wait|pause)$"
)


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s'-]", "", text.lower()).strip()


def is_backchannel(text: str) -> bool:
    return bool(_BACKCHANNEL.match(_norm(text)))


def is_stop(text: str) -> bool:
    return bool(_STOP.match(_norm(text)))


class Agent(Protocol):
    def run_turn(self, prompt: str) -> Any: ...  # AsyncIterator[AgentEvent]

    async def interrupt(self) -> None: ...


class VoiceConversation:
    def __init__(
        self,
        agent: Agent,
        speaker: Speaker,
        stt: STT | None,
        events: asyncio.Queue[InputEvent],
        gate: VoicePermissionGate,
        console: Console,
    ) -> None:
        self.agent = agent
        self.speaker = speaker
        self.stt = stt
        self.events = events
        self.gate = gate
        self.console = console
        self._turn: asyncio.Task[None] | None = None
        self._generation = 0
        self._barged_in = False

    @property
    def agent_busy(self) -> bool:
        return self._turn is not None and not self._turn.done()

    async def run(self) -> None:
        speaker_task = asyncio.create_task(self.speaker.run())
        self.console.listening()
        try:
            while True:
                event = await self.events.get()
                if isinstance(event, Quit):
                    break
                if isinstance(event, SpeechStarted):
                    self.on_speech_started()
                elif isinstance(event, Utterance):
                    text = event.text
                    if text is None:
                        assert self.stt is not None
                        self.console.transcribing()
                        text = await self.stt.transcribe(event.audio)
                    await self.on_utterance(text)
        finally:
            await self.cancel_turn()
            await self.speaker.wait_idle()
            speaker_task.cancel()

    # -- input handling -----------------------------------------------------

    def on_speech_started(self) -> None:
        if self.speaker.busy:
            self.speaker.hold()
            self._barged_in = True

    async def on_utterance(self, text: str) -> None:
        text = text.strip()
        barged_in, self._barged_in = self._barged_in, False

        if self.gate.waiting:
            if not text:
                self.speaker.resume()
                return
            self.console.user(text)
            self.speaker.clear()
            self.gate.answer(text)
            return

        if not text or (barged_in and is_backchannel(text)):
            if text:
                self.console.user(text, note="(carry on)")
            self.speaker.resume()
            return

        self.console.user(text)

        if is_stop(text):
            self.speaker.clear()
            if self.agent_busy:
                self.console.note("interrupting Claude")
                await self.cancel_turn()
            self.console.listening()
            return

        heard = self.speaker.last_spoken if barged_in else None
        was_busy = self.agent_busy
        self.speaker.clear()
        if was_busy:
            await self.cancel_turn()
        prompt = text
        if barged_in or was_busy:
            note = "[Interrupted: the user cut you off"
            if heard:
                note += f'; the last thing they heard you say was "{heard}"'
            note += "]"
            prompt = f"{note}\n{text}"
        self.start_turn(prompt)

    # -- agent turns --------------------------------------------------------

    def start_turn(self, prompt: str) -> None:
        self._generation += 1
        self._turn = asyncio.create_task(self._run_turn(prompt, self._generation))

    async def cancel_turn(self) -> None:
        if not self.agent_busy:
            return
        self._generation += 1  # anything still streaming from the old turn is muted
        self.gate.cancel()
        try:
            await self.agent.interrupt()
            await asyncio.wait_for(asyncio.shield(self._turn), timeout=15)
        except asyncio.TimeoutError:
            self.console.note("Claude didn't stop in time; cancelling")
            self._turn.cancel()
        except Exception as exc:  # noqa: BLE001 - never let a bad interrupt kill the loop
            self.console.note(f"interrupt failed: {exc}")

    async def _run_turn(self, prompt: str, generation: int) -> None:
        chunker = SpeechChunker()
        self.console.thinking()

        def live() -> bool:
            return generation == self._generation

        try:
            async for event in self.agent.run_turn(prompt):
                if not live():
                    continue  # drain the interrupted turn without speaking it
                self._handle(event, chunker)
        except Exception as exc:  # noqa: BLE001
            if live():
                self.console.note(f"agent error: {exc}")
                self.speaker.say("Sorry, something went wrong on my end.")
        finally:
            if live():
                for sentence in chunker.flush():
                    self.speaker.say(sentence)
                self.console.end_reply()
                self.console.listening()

    def _handle(self, event: AgentEvent, chunker: SpeechChunker) -> None:
        if isinstance(event, TextDelta):
            self.console.claude_text(event.text)
            for sentence in chunker.feed(event.text):
                self.speaker.say(sentence)
        elif isinstance(event, TextBlockEnd):
            for sentence in chunker.flush():
                self.speaker.say(sentence)
        elif isinstance(event, ToolStarted):
            self.console.tool(event.name, event.input)
        elif isinstance(event, TurnDone):
            self.console.done(event.result)
