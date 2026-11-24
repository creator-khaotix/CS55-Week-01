"""Turn Claude's streamed markdown into short, speakable chunks.

Claude Code answers in markdown: code fences, tables, bullet lists, URLs.
None of that should be read aloud verbatim. The terminal still shows the full
text; this module decides what the voice says.
"""

from __future__ import annotations

import re

CODE_NOTICE = "I've put the code on screen."
TABLE_NOTICE = "There's a table on screen."

_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://([^/\s)]+)\S*")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_EMPHASIS = re.compile(r"(\*\*|\*|~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SPACES = re.compile(r"\s+")
# A sentence boundary: terminal punctuation (plus closing quotes/brackets)
# followed by whitespace.
_BOUNDARY = re.compile(r"[.!?…]+[\"')\]]*\s+")


def clean_for_speech(text: str) -> str:
    """Strip markdown syntax from a single line or sentence."""
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMPHASIS.sub("", text)
    return _SPACES.sub(" ", text).strip()


def _speakable(text: str) -> list[str]:
    cleaned = clean_for_speech(text)
    return [cleaned] if any(c.isalnum() for c in cleaned) else []


class SpeechChunker:
    """Incrementally split streamed text into sentences ready for TTS.

    Feed it text deltas as they arrive; it returns whatever complete sentences
    are ready, so speech can start long before Claude finishes writing.
    """

    def __init__(self, min_chars: int = 20, max_chars: int = 280) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._line = ""  # current line, not yet newline-terminated
        self._emitted = 0  # chars of the current line already spoken
        self._in_code = False
        self._in_table = False
        self._last = ""  # last sentence emitted

    def feed(self, delta: str) -> list[str]:
        out: list[str] = []
        self._line += delta
        while "\n" in self._line:
            line, self._line = self._line.split("\n", 1)
            out.extend(self._end_line(line))
        out.extend(self._partial_line())
        return self._track(out)

    def flush(self) -> list[str]:
        """Emit everything left, e.g. at the end of a text block or turn."""
        line, self._line = self._line, ""
        out = self._end_line(line)
        self._in_table = False
        return self._track(out)

    def _track(self, out: list[str]) -> list[str]:
        # Skip "it's on screen" notices when Claude just said as much itself.
        kept = []
        for s in out:
            if s in (CODE_NOTICE, TABLE_NOTICE) and "screen" in self._last.lower():
                continue
            kept.append(s)
            self._last = s
        return kept

    def _end_line(self, line: str) -> list[str]:
        rest, self._emitted = line[self._emitted :], 0
        stripped = line.strip()
        if stripped.startswith("```"):
            self._in_code = not self._in_code
            return [CODE_NOTICE] if self._in_code else []
        if self._in_code:
            return []
        if stripped.startswith("|"):
            if self._in_table:
                return []
            self._in_table = True
            return [TABLE_NOTICE]
        self._in_table = False
        # A newline ends whatever prose is pending (list items, headings...).
        out: list[str] = []
        while (cut := self._find_cut(rest)) is not None:
            out.extend(_speakable(rest[:cut]))
            rest = rest[cut:]
        return out + _speakable(rest)

    def _partial_line(self) -> list[str]:
        """Emit complete sentences from the unfinished current line."""
        if self._in_code or self._line.lstrip().startswith(("`", "|")):
            return []  # might be a fence or table row; wait for the newline
        out: list[str] = []
        while (cut := self._find_cut(self._line[self._emitted :])) is not None:
            out.extend(_speakable(self._line[self._emitted : self._emitted + cut]))
            self._emitted += cut
        return out

    def _find_cut(self, text: str) -> int | None:
        for m in _BOUNDARY.finditer(text):
            if m.end() >= self.min_chars:
                return m.end()
        if len(text) > self.max_chars:
            # No sentence end in sight; break at the last comma or space.
            for sep in (", ", "; ", " "):
                i = text.rfind(sep, 0, self.max_chars)
                if i > self.min_chars:
                    return i + len(sep)
        return None
