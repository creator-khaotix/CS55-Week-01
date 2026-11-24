"""The Claude Code side: a persistent ClaudeSDKClient session plus voice approvals.

This is the same agent you get in the terminal (same tools, CLAUDE.md, MCP
servers, settings and permission rules from ~/.claude and the project), just
driven from speech instead of a keyboard.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    ToolPermissionContext,
    ToolUseBlock,
)

VOICE_SYSTEM_PROMPT = """\
# Voice mode
The user is talking to you by voice, like a phone call. Everything you write
between tool calls is converted to speech; the full text, code and tool output
also appear in their terminal.

- Talk like a person on a call: short, plain sentences. Usually one to three
  sentences per reply unless they ask for detail.
- No markdown in prose: no headings, bold, bullet lists or tables. If a list
  is unavoidable, say it as a sentence ("three things: A, B and C").
- Put code, long paths, command output and URLs in code blocks; they are shown
  on screen but not read aloud. Refer to them ("the command's on screen").
- Before a tool call that could take a while, say one short sentence about
  what you're doing ("Checking the Tailscale status now."), so the user isn't
  left in silence. Then report the result in a sentence or two, not a dump.
- Speech-to-text makes mistakes. If a name, host or path sounds garbled,
  make the obvious correction or ask, rather than acting on a wrong guess.
- The user can interrupt you at any time. A message starting with
  "[Interrupted" means they cut you off: drop what you were saying and deal
  with what they just said. The note tells you what they actually heard.
- Ask before destructive or hard-to-undo actions. The user approves tool
  requests by saying yes or no.
"""

# Tools that never need a spoken "may I?" (read-only, or purely internal).
DEFAULT_AUTO_ALLOW = frozenset(
    {"Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent"}
)

_YES = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|go|go ahead|do it|please|approved?|"
    r"sounds good|alright|all right|affirmative|correct|right)\b"
)
_NO = re.compile(r"^(no|nope|nah|don'?t|do not|stop|cancel|wait|hold on|never ?mind|negative)\b")


def parse_yes_no(text: str) -> bool | None:
    norm = re.sub(r"[^\w\s']", "", text.lower()).strip()
    if _NO.match(norm):
        return False
    if _YES.match(norm):
        return True
    return None


def describe_tool(name: str, tool_input: dict[str, Any]) -> str:
    """A short spoken description of a tool call, e.g. 'run: ls -la'."""
    if name == "Bash":
        cmd = str(tool_input.get("command", "")).strip()
        desc = tool_input.get("description")
        if desc:
            return f"run a command to {desc[0].lower()}{desc[1:]}"
        return f"run: {cmd[:120]}" if cmd else "run a shell command"
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "a file")
        verb = "create or overwrite" if name == "Write" else "edit"
        return f"{verb} {path.rsplit('/', 1)[-1]}"
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        return f"use {tool.replace('_', ' ')} from {server}"
    return f"use the {name} tool"


class VoicePermissionGate:
    """Bridges Claude Code permission prompts to a spoken yes/no.

    Only consulted when Claude Code would otherwise prompt in the terminal, so
    your existing allow/deny rules in settings.json still apply first.
    """

    REASKS = 2  # times to repeat the question after an unclear short reply

    def __init__(
        self,
        ask: Callable[[str], None],
        show: Callable[[str, dict[str, Any]], None],
        auto_allow: frozenset[str] = DEFAULT_AUTO_ALLOW,
        timeout: float = 45.0,
    ) -> None:
        self._ask = ask
        self._show = show
        self._auto_allow = auto_allow
        self._timeout = timeout
        self._pending: asyncio.Future[str] | None = None

    @property
    def waiting(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def answer(self, text: str) -> None:
        if self.waiting:
            self._pending.set_result(text)

    def cancel(self) -> None:
        if self.waiting:
            self._pending.cancel()

    async def can_use_tool(
        self, name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if name in self._auto_allow:
            return PermissionResultAllow()
        self._show(name, tool_input)
        question = f"I'd like to {describe_tool(name, tool_input)}. Okay?"
        for _ in range(1 + self.REASKS):
            self._pending = asyncio.get_running_loop().create_future()
            self._ask(question)
            try:
                reply = await asyncio.wait_for(self._pending, self._timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return PermissionResultDeny(message="No answer from the user; not approved.")
            finally:
                self._pending = None
            verdict = parse_yes_no(reply)
            # A few stray words are usually background noise or Whisper
            # inventing "thanks for watching" from silence; ask again rather
            # than treat them as an answer.
            if verdict is not None or len(reply.split()) > 4:
                break
            question = "Sorry, was that a yes or a no?"
        if verdict:
            return PermissionResultAllow()
        if verdict is False:
            return PermissionResultDeny(message=f'The user declined by voice: "{reply}"')
        return PermissionResultDeny(
            message=f'Not approved. Instead of yes/no the user said: "{reply}". '
            "Take that into account before trying again."
        )


# -- events the conversation consumes ---------------------------------------


@dataclass
class TextDelta:
    text: str


@dataclass
class TextBlockEnd:
    pass


@dataclass
class ToolStarted:
    name: str
    input: dict[str, Any]


@dataclass
class TurnDone:
    result: ResultMessage | None


AgentEvent = TextDelta | TextBlockEnd | ToolStarted | TurnDone


class ClaudeCodeAgent:
    def __init__(self, options: ClaudeAgentOptions) -> None:
        options.include_partial_messages = True
        self._client = ClaudeSDKClient(options)
        self.session_id: str | None = None

    async def connect(self) -> None:
        await self._client.connect()

    async def close(self) -> None:
        await self._client.disconnect()

    async def interrupt(self) -> None:
        await self._client.interrupt()

    async def run_turn(self, prompt: str) -> AsyncIterator[AgentEvent]:
        await self._client.query(prompt)
        async for msg in self._client.receive_response():
            # Subagent chatter (parent_tool_use_id set) is not spoken.
            if isinstance(msg, StreamEvent) and msg.parent_tool_use_id is None:
                ev = msg.event
                kind = ev.get("type")
                if kind == "content_block_delta":
                    delta = ev.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield TextDelta(delta.get("text", ""))
                elif kind == "content_block_stop":
                    yield TextBlockEnd()
            elif isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        yield ToolStarted(block.name, block.input)
            elif isinstance(msg, ResultMessage):
                self.session_id = msg.session_id
                yield TurnDone(msg)


def build_options(
    gate: VoicePermissionGate,
    *,
    cwd: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
    continue_conversation: bool = False,
    resume: str | None = None,
    extra_system_prompt: str | None = None,
) -> ClaudeAgentOptions:
    append = VOICE_SYSTEM_PROMPT + (f"\n{extra_system_prompt}\n" if extra_system_prompt else "")
    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": append},
        can_use_tool=gate.can_use_tool,
        include_partial_messages=True,
        cwd=cwd,
        model=model,
        permission_mode=permission_mode,
        continue_conversation=continue_conversation,
        resume=resume,
    )

