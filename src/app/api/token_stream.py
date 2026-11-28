"""Answer text while it is generated (`token` events of `/v1/chat/stream`, contracts/events.md).

The graph is streamed with LangGraph's ``messages`` mode, so every model call of the turn arrives
here as chunks. Only the text the merchant will read is forwarded:

- with one specialist in the plan, that specialist's answer (the ``answer`` field of the knowledge
  specialist's structured output, or the support specialist's final text);
- with several, only the synthesis. Classifier, planner, search rewrite and verifier never show.

The output guardrail has not run yet when this text leaves, so what it does deterministically is
applied to the stream itself, and the rest is covered by the final event:

- **PII**: the accumulated text is masked before anything is emitted, and the last ``HOLDBACK``
  characters and the trailing word stay back, so a document, card, phone or e-mail is never sent
  in pieces before the mask can see it whole;
- **financial promise**: the stream stops for good as soon as the text matches (the matched
  phrase is still inside the held-back tail); the final event brings the rewritten answer;
- **grounding** is a model call on the finished answer. The ``done`` event always carries the
  verified response, and the client replaces the draft with it. Until then the UI labels the
  draft as not verified yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessageChunk

from app.core.llm import STREAM_ROLES
from app.guardrails.output import has_financial_promise
from app.guardrails.pii import mask

HOLDBACK = 32  # longer than any masked pattern with separators (a 19-digit card with spaces: 23)
_ANSWER_KEY = re.compile(r'^\s*\{\s*"answer"\s*:\s*"')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_SPECIALISTS = frozenset({"knowledge", "support"})


class AnswerFieldReader:
    """Decodes the ``answer`` string of a JSON object that arrives in pieces.

    The structured output of the knowledge specialist is ``{"answer": "...", "confidence": ...,
    "cited": [...]}``; ``answer`` is the first field, so its text can be read while the object is
    still incomplete. If the object does not start with ``answer``, nothing is read (no stream,
    the final event still brings the answer)."""

    def __init__(self) -> None:
        self._buffer = ""
        self._pos: int | None = None
        self._closed = False

    def feed(self, piece: str) -> str:
        if self._closed or not piece:
            return ""
        self._buffer += piece
        if self._pos is None:
            match = _ANSWER_KEY.match(self._buffer)
            if match is None:
                # Not (yet) `{"answer": "`: wait while it can still become it, else give up.
                if len(self._buffer) > 64:
                    self._closed = True
                return ""
            self._pos = match.end()
        out: list[str] = []
        buf, i = self._buffer, self._pos
        while i < len(buf):
            char = buf[i]
            if char == '"':
                self._closed = True
                break
            if char != "\\":
                out.append(char)
                i += 1
                continue
            if i + 1 >= len(buf):
                break  # escape cut in two: wait for the rest
            kind = buf[i + 1]
            if kind != "u":
                out.append(_ESCAPES.get(kind, kind))
                i += 2
                continue
            decoded = _unicode_escape(buf, i)
            if decoded is None:
                break
            text, i = decoded
            out.append(text)
        self._pos = i
        return "".join(out)


def _unicode_escape(buf: str, i: int) -> tuple[str, int] | None:
    r"""``\uXXXX`` at ``i`` (and its low surrogate when it is a high one); None if incomplete."""
    if i + 6 > len(buf):
        return None
    try:
        code = int(buf[i + 2 : i + 6], 16)
    except ValueError:
        return "", i + 6
    if not 0xD800 <= code <= 0xDBFF:
        return chr(code), i + 6
    if i + 12 > len(buf):
        return None
    if buf[i + 6 : i + 8] != "\\u":
        return "", i + 6
    try:
        low = int(buf[i + 8 : i + 12], 16)
    except ValueError:
        return "", i + 12
    return chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)), i + 12


def safe_prefix(raw: str) -> str | None:
    """What may be shown of ``raw`` so far: masked, without the held-back tail. ``None`` when
    the text contains a financial promise (the stream must stop)."""
    if has_financial_promise(raw):
        return None
    masked, _ = mask(raw)
    cut = len(masked) - HOLDBACK
    if cut <= 0:
        return ""
    # Never cut inside a word: back off to the last whitespace at or before the cut.
    boundary = max(masked.rfind(" ", 0, cut + 1), masked.rfind("\n", 0, cut + 1))
    return masked[: boundary + 1] if boundary >= 0 else ""


class TokenStream:
    """State of one turn: which generation is visible and how much of it was already sent."""

    def __init__(self) -> None:
        self._agents: list[str] = []
        self._generation: str | None = None  # id of the model call being shown
        self._reader: AnswerFieldReader | None = None
        self._raw = ""
        self._sent = ""
        self._stopped = False
        self.sent_any = False

    def observe_update(self, update: dict[str, Any]) -> None:
        """Keeps the agents of the current plan, read from the supervisor's state update."""
        plan = (update.get("supervisor") or {}).get("plan") if isinstance(update, dict) else None
        if isinstance(plan, dict):
            self._agents = [str(s.get("agent")) for s in plan.get("subtasks") or []]

    def _visible(self, role: str) -> bool:
        if role == "synthesize":
            return True
        return role in _SPECIALISTS and self._agents == [role]

    def feed(self, chunk: Any, metadata: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Events (``{"event": ..., "data": {...}}``) for one model chunk; usually none or one."""
        role = str(metadata.get("llm_role") or "")
        if role not in STREAM_ROLES or not isinstance(chunk, AIMessageChunk):
            return
        if not self._visible(role):
            return
        generation = str(chunk.id or "")
        if generation != self._generation:
            # A new model call (a replan, or the support specialist after its tools): what was
            # shown belongs to an answer that is being replaced.
            if self._sent:
                yield {"event": "token_reset", "data": {}}
            self._generation, self._raw, self._sent, self._stopped = generation, "", "", False
            self._reader = AnswerFieldReader() if role == "knowledge" else None
        if self._stopped:
            return
        self._raw += self._text(chunk)
        prefix = safe_prefix(self._raw)
        if prefix is None or not prefix.startswith(self._sent):
            # A promise, or the mask changed text already sent: say no more; `done` has the
            # final answer.
            self._stopped = True
            return
        delta = prefix[len(self._sent) :]
        if delta:
            self._sent = prefix
            self.sent_any = True
            yield {"event": "token", "data": {"text": delta}}

    def _text(self, chunk: AIMessageChunk) -> str:
        content = chunk.content if isinstance(chunk.content, str) else ""
        if self._reader is None:
            return content
        # Structured output arrives as JSON text: in `content` (json_schema) or in the arguments
        # of a tool call (function calling), depending on the provider method.
        pieces = content + "".join(str(c.get("args") or "") for c in chunk.tool_call_chunks or [])
        return self._reader.feed(pieces)
