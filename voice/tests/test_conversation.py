"""Barge-in and approval behaviour, with a scripted agent and a fake voice."""

import asyncio

import pytest

from claude_voice.agent import (
    TextBlockEnd,
    TextDelta,
    TurnDone,
    VoicePermissionGate,
    describe_tool,
    parse_yes_no,
)
from claude_voice.conversation import VoiceConversation, is_backchannel, is_echo, is_stop
from claude_voice.events import Quit, SpeechStarted, Utterance
from claude_voice.tts import Speaker
from claude_voice.ui import Console


class FakeTTS:
    def __init__(self, seconds: float = 0.05) -> None:
        self.seconds = seconds
        self.spoken: list[str] = []
        self.cut: list[str] = []
        self._stop = asyncio.Event()

    async def speak(self, text: str) -> None:
        self._stop.clear()
        try:
            await asyncio.wait_for(self._stop.wait(), self.seconds)
            self.cut.append(text)
        except asyncio.TimeoutError:
            self.spoken.append(text)

    def stop(self) -> None:
        self._stop.set()


class FakeAgent:
    """Streams a canned reply slowly; records prompts and interrupts."""

    def __init__(
        self,
        reply: str = "One. Two is here. Three is next. Four is last.",
        gate=None,
        word_delay: float = 0.01,
    ):
        self.reply = reply
        self.word_delay = word_delay
        self.prompts: list[str] = []
        self.interrupts = 0
        self.gate = gate
        self._interrupted = asyncio.Event()

    async def interrupt(self) -> None:
        self.interrupts += 1
        self._interrupted.set()

    async def run_turn(self, prompt: str):
        self.prompts.append(prompt)
        self._interrupted.clear()
        if self.gate is not None and "tool" in prompt:
            verdict = await self.gate.can_use_tool("Bash", {"command": "rm -rf build"}, None)
            yield TextDelta(f"verdict {verdict.behavior}. ")
        for word in self.reply.split(" "):
            if self._interrupted.is_set():
                break
            yield TextDelta(word + " ")
            await asyncio.sleep(self.word_delay)
        yield TextBlockEnd()
        yield TurnDone(None)


def make(agent=None, tts=None):
    tts = tts or FakeTTS()
    speaker = Speaker(tts)
    console = Console(color=False)
    gate = VoicePermissionGate(ask=speaker.say, show=lambda *a: None)
    agent = agent or FakeAgent()
    if isinstance(agent, FakeAgent) and agent.gate is None:
        agent.gate = gate
    events: asyncio.Queue = asyncio.Queue()
    convo = VoiceConversation(agent, speaker, None, events, gate, console)
    return convo, agent, tts, events


async def say(events, text, settle=0.0):
    events.put_nowait(SpeechStarted())
    events.put_nowait(Utterance(text=text))
    await asyncio.sleep(settle)


async def finish(convo, events):
    events.put_nowait(Quit())
    await asyncio.wait_for(convo.run_task, 5)


@pytest.fixture
def started():
    def _start(*a, **kw):
        convo, agent, tts, events = make(*a, **kw)
        convo.run_task = asyncio.create_task(convo.run())
        return convo, agent, tts, events

    return _start


async def test_plain_turn_is_spoken_sentence_by_sentence(started):
    convo, agent, tts, events = started()
    await say(events, "count to four", settle=0.5)
    await finish(convo, events)
    assert agent.prompts == ["count to four"]
    assert tts.spoken == ["One. Two is here. Three is next.", "Four is last."]


async def test_backchannel_resumes_the_cut_off_sentence(started):
    convo, agent, tts, events = started(tts=FakeTTS(seconds=0.15))
    await say(events, "count to four", settle=0.2)
    await say(events, "mm-hm", settle=1.0)
    await finish(convo, events)
    assert agent.prompts == ["count to four"]  # "mm-hm" never reached Claude
    assert tts.cut  # something was cut off...
    assert tts.cut[0] in tts.spoken  # ...and then replayed in full


async def test_real_interruption_stops_agent_and_sends_new_prompt(started):
    convo, agent, tts, events = started(agent=FakeAgent(word_delay=0.1))
    await say(events, "count to four", settle=0.45)
    await say(events, "actually, what time is it", settle=1.5)
    await finish(convo, events)
    assert agent.interrupts == 1
    assert agent.prompts[1].startswith("[Interrupted")
    assert agent.prompts[1].endswith("actually, what time is it")


async def test_stop_word_silences_everything(started):
    convo, agent, tts, events = started(tts=FakeTTS(seconds=0.2))
    await say(events, "count to four", settle=0.1)
    await say(events, "stop", settle=0.5)
    await finish(convo, events)
    assert agent.prompts == ["count to four"]
    assert agent.interrupts == 1
    assert not convo.speaker.busy


async def test_tool_approval_by_voice(started):
    convo, agent, tts, events = started(agent=FakeAgent(reply="Done."))
    await say(events, "use a tool", settle=0.2)
    await say(events, "yes go ahead", settle=0.5)
    await finish(convo, events)
    assert any("Okay?" in s for s in tts.spoken + tts.cut)
    assert any(s.startswith("verdict allow") for s in tts.spoken)
    assert len(agent.prompts) == 1  # the "yes" answered the gate, not a new turn


async def test_tool_denial_by_voice(started):
    convo, agent, tts, events = started(agent=FakeAgent(reply="Okay then."))
    await say(events, "use a tool", settle=0.2)
    await say(events, "no, don't do that", settle=0.5)
    await finish(convo, events)
    assert any(s.startswith("verdict deny") for s in tts.spoken)


def test_phrase_classifiers():
    assert is_backchannel("Mm-hm.") and is_backchannel("okay") and is_backchannel("go on")
    assert not is_backchannel("okay so what about the NAS")
    assert is_stop("Stop!") and is_stop("never mind")
    assert not is_stop("stop the docker container on the nas")
    assert parse_yes_no("Yeah, go ahead.") is True
    assert parse_yes_no("No.") is False
    assert parse_yes_no("use rsync instead") is None


def test_describe_tool():
    assert describe_tool("Bash", {"command": "ls", "description": "List files"}) == (
        "run a command to list files"
    )
    assert describe_tool("Edit", {"file_path": "/a/b/notes.md"}) == "edit notes.md"
    assert describe_tool("mcp__obsidian__search_notes", {}) == "use search notes from obsidian"


class LateCommandAgent(FakeAgent):
    """Like real Claude: finishes writing a code block after being interrupted."""

    async def run_turn(self, prompt: str):
        self.prompts.append(prompt)
        self._interrupted.clear()
        if len(self.prompts) == 1:
            yield TextDelta("Run this: ")
            await self._interrupted.wait()
            yield TextDelta("```\nsh build.sh\n```")
        yield TextBlockEnd()
        yield TurnDone(None)


async def test_text_written_after_a_cut_off_still_reaches_the_screen(started, capsys):
    convo, agent, tts, events = started(agent=LateCommandAgent())
    await say(events, "how do I build it", settle=0.2)
    await say(events, "wait, hold on", settle=0.5)
    await finish(convo, events)
    out = capsys.readouterr().out
    assert "sh build.sh" in out  # on screen...
    assert "cut off" in out
    assert not any("build.sh" in s for s in tts.spoken)  # ...but never spoken


def test_echo_classifier():
    said = "One. Two is here. Three is next."
    assert is_echo("two is here three", said)
    assert is_echo("Three is next.", said)
    assert not is_echo("what time is it", said)
    assert not is_echo("no", said)
    assert not is_echo("anything", "")


async def test_own_echo_does_not_interrupt(started):
    convo, agent, tts, events = started(tts=FakeTTS(seconds=0.15))
    await say(events, "count to four", settle=0.2)
    await say(events, "two is here three is next", settle=1.0)
    await finish(convo, events)
    assert agent.prompts == ["count to four"]  # the echo never reached Claude
    assert agent.interrupts == 0
    assert tts.cut and tts.cut[0] in tts.spoken  # paused, then replayed


def test_echo_classifier():
    said = "One. Two is here. Three is next."
    assert is_echo("two is here three", said)
    assert is_echo("Three is next.", said)
    assert not is_echo("what time is it", said)
    assert not is_echo("no", said)
    assert not is_echo("anything", "")


async def test_own_echo_does_not_interrupt(started):
    convo, agent, tts, events = started(tts=FakeTTS(seconds=0.15))
    await say(events, "count to four", settle=0.2)
    await say(events, "two is here three is next", settle=1.0)
    await finish(convo, events)
    assert agent.prompts == ["count to four"]  # the echo never reached Claude
    assert agent.interrupts == 0
    assert tts.cut and tts.cut[0] in tts.spoken  # paused, then replayed


async def test_unclear_approval_is_asked_again(started):
    convo, agent, tts, events = started(agent=FakeAgent(reply="Done."))
    await say(events, "use a tool", settle=0.2)
    await say(events, "Thanks for your question.", settle=0.3)
    await say(events, "yes", settle=0.5)
    await finish(convo, events)
    assert any("yes or a no" in s for s in tts.spoken + tts.cut)
    assert any(s.startswith("verdict allow") for s in tts.spoken)
    assert len(agent.prompts) == 1


async def test_unclear_approval_is_asked_again(started):
    convo, agent, tts, events = started(agent=FakeAgent(reply="Done."))
    await say(events, "use a tool", settle=0.2)
    await say(events, "Thanks for your question.", settle=0.3)
    await say(events, "yes", settle=0.5)
    await finish(convo, events)
    assert any("yes or a no" in s for s in tts.spoken + tts.cut)
    assert any(s.startswith("verdict allow") for s in tts.spoken)
    assert len(agent.prompts) == 1
