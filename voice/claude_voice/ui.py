"""Terminal output: the full transcript, tool calls and state, alongside the voice."""

from __future__ import annotations

import json
import sys
from typing import Any

DIM, BOLD, CYAN, GREEN, YELLOW, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[0m"


class Console:
    def __init__(self, color: bool | None = None, verbose: bool = False) -> None:
        self.color = sys.stdout.isatty() if color is None else color
        self.verbose = verbose
        self._mid_reply = False

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def _line(self, text: str) -> None:
        if self._mid_reply:
            print()
            self._mid_reply = False
        print(text, flush=True)

    def status(self, text: str) -> None:
        self._line(self._c(DIM, f"· {text}"))

    def listening(self) -> None:
        self.status("listening")

    def transcribing(self) -> None:
        if self.verbose:
            self.status("transcribing")

    def thinking(self) -> None:
        self.status("thinking")

    def note(self, text: str) -> None:
        self._line(self._c(YELLOW, f"! {text}"))

    def user(self, text: str, note: str | None = None) -> None:
        suffix = f" {self._c(DIM, note)}" if note else ""
        self._line(f"{self._c(BOLD + GREEN, 'you')}  {text}{suffix}")

    def claude_text(self, delta: str, cut_off: bool = False) -> None:
        if not self._mid_reply:
            label = self._c(BOLD + CYAN, "claude") + (self._c(DIM, " (cut off, not spoken)") if cut_off else "")
            print(f"{label} ", end="")
            self._mid_reply = True
        print(delta, end="", flush=True)

    def end_reply(self) -> None:
        if self._mid_reply:
            print(flush=True)
            self._mid_reply = False

    def tool(self, name: str, tool_input: dict[str, Any]) -> None:
        if name == "Bash":
            detail = tool_input.get("command", "")
        elif "file_path" in tool_input:
            detail = tool_input["file_path"]
        else:
            detail = json.dumps(tool_input)[:200]
        self._line(self._c(YELLOW, f"⚙ {name}") + f" {detail}")

    def permission(self, name: str, tool_input: dict[str, Any]) -> None:
        self._line(self._c(BOLD + YELLOW, f"? approve {name}?"))
        print(json.dumps(tool_input, indent=2)[:2000], flush=True)

    def done(self, result: Any) -> None:
        if self.verbose and result is not None and result.total_cost_usd is not None:
            self.status(f"turn done · {result.num_turns} steps · ${result.total_cost_usd:.4f}")
