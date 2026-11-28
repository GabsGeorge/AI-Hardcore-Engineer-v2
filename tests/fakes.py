"""Deterministic test doubles (ADR 0006): scripted chat models, embeddings and reranker."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import BaseModel, ValidationError

DEFAULT_USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


@dataclass
class Scripted:
    """A scripted reply with explicit token usage."""

    value: Any
    tokens: int = DEFAULT_USAGE["total_tokens"]


def _usage(tokens: int) -> dict[str, int]:
    return {
        "input_tokens": tokens - tokens // 6,
        "output_tokens": tokens // 6,
        "total_tokens": tokens,
    }


def _as_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, BaseMessage):
        return str(payload.content)
    if isinstance(payload, list | tuple):
        return "\n".join(_as_text(p) for p in payload)
    if hasattr(payload, "to_messages"):
        return _as_text(payload.to_messages())
    return str(payload)


@dataclass
class FakeLLM:
    """Registry of scripted replies per role. Install with ``llm.set_overrides``."""

    scripts: dict[str, deque[Any]] = field(default_factory=lambda: defaultdict(deque))
    calls: list[tuple[str, str]] = field(default_factory=list)

    def script(self, **by_role: list[Any]) -> FakeLLM:
        for role, items in by_role.items():
            self.scripts[role].extend(items)
        return self

    def model(self, role: str) -> FakeChatModel:
        return FakeChatModel(self, role)

    def prompts(self, role: str | None = None) -> str:
        return "\n".join(text for r, text in self.calls if role in (None, r))

    def next(self, role: str, payload: Any) -> Scripted:
        self.calls.append((role, _as_text(payload)))
        if not self.scripts[role]:
            raise AssertionError(f"no scripted reply left for role {role!r}")
        item = self.scripts[role].popleft()
        return item if isinstance(item, Scripted) else Scripted(item)


@dataclass
class FakeChatModel:
    llm: FakeLLM
    role: str

    async def ainvoke(self, payload: Any, config: Any = None, **_: Any) -> AIMessage:
        reply = self.llm.next(self.role, payload)
        if isinstance(reply.value, Exception):
            raise reply.value
        if isinstance(reply.value, AIMessage):
            return reply.value
        return AIMessage(content=str(reply.value), usage_metadata=_usage(reply.tokens))

    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool = False, **_: Any
    ) -> _Structured:
        return _Structured(self, schema, include_raw)

    def bind_tools(self, *_: Any, **__: Any) -> FakeChatModel:
        return self


@dataclass
class _Structured:
    model: FakeChatModel
    schema: type[BaseModel]
    include_raw: bool

    async def ainvoke(self, payload: Any, config: Any = None, **_: Any) -> Any:
        reply = self.model.llm.next(self.model.role, payload)
        raw = AIMessage(content="", usage_metadata=_usage(reply.tokens))
        parsed: BaseModel | None = None
        error: Exception | None = None
        if isinstance(reply.value, Exception):
            error = reply.value
        else:
            try:
                parsed = (
                    reply.value
                    if isinstance(reply.value, self.schema)
                    else self.schema.model_validate(reply.value)
                )
            except ValidationError as exc:
                error = exc
        if self.include_raw:
            return {"raw": raw, "parsed": parsed, "parsing_error": error}
        if error:
            raise error
        return parsed


def fake_embeddings() -> DeterministicFakeEmbedding:
    return DeterministicFakeEmbedding(size=1536)


class FakeReranker:
    """Scores passages by word overlap with the query (0..1), highest first."""

    def rerank(self, query: str, passages: list[str]) -> list[tuple[int, float]]:
        terms = set(query.lower().split())
        scores = [
            (i, len(terms & set(p.lower().split())) / max(len(terms), 1))
            for i, p in enumerate(passages)
        ]
        return sorted(scores, key=lambda s: s[1], reverse=True)


@dataclass
class SpyGraph:
    """Stands in for the compiled graph when a test only checks the API boundary."""

    calls: int = 0
    last_input: Any = None
    last_config: Any = None
    reply: str = "ok"

    async def ainvoke(self, payload: Any, config: Any = None, **_: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_input, self.last_config = payload, config
        return {
            "final_response": self.reply,
            "route_trace": [],
            "results": [],
            "guardrail_flags": [],
            "escalation": None,
        }


def make_plan(
    *agents: str,
    language: str = "pt",
    confidence: float = 0.9,
    sensitive: bool = False,
    query: str = "pergunta",
) -> dict[str, Any]:
    """A valid supervisor plan routing one subtask to each agent given."""
    return {
        "route": list(agents),
        "subtasks": [
            {"id": f"s{i}", "agent": agent, "query": query} for i, agent in enumerate(agents, 1)
        ],
        "confidence": confidence,
        "rationale": "test plan",
        "language": language,
        "sensitive": sensitive,
    }


# --- Streaming fakes -----------------------------------------------------------------------
# A real LangChain chat model (BaseChatModel) that streams its scripted reply in small pieces,
# so LangGraph's "messages" stream mode sees chunks exactly as it does with a provider model:
# plain text in `content`, structured output as the JSON arguments of a tool call.


class StreamingFakeChat(BaseChatModel):
    llm: Any  # the FakeLLM registry the scripts come from
    role: str
    tool_name: str | None = None
    piece: int = 7

    @property
    def _llm_type(self) -> str:
        return "streaming-fake"

    def bind_tools(self, tools: Any, **_: Any) -> StreamingFakeChat:
        schema = tools[0]
        name = getattr(schema, "__name__", None) or schema.get("title", "tool")
        return self.model_copy(update={"tool_name": name})

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("the graph only calls the async API")

    def _chunks(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        reply = self.llm.next(self.role, messages)
        value = reply.value
        if isinstance(value, Exception):
            raise value
        usage: Any = _usage(reply.tokens)
        if isinstance(value, AIMessage) and value.tool_calls:  # the support tool loop
            calls: list[Any] = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(value.tool_calls)
            ]
            return [AIMessageChunk(content="", tool_call_chunks=calls, usage_metadata=usage)]
        if self.tool_name is not None and not isinstance(value, AIMessage):  # structured output
            if isinstance(value, BaseModel):
                value = value.model_dump()
            text = json.dumps(value, ensure_ascii=False)
            parts = [text[i : i + self.piece] for i in range(0, len(text), self.piece)]
            out = []
            for n, part in enumerate(parts):
                call: Any = {
                    "name": self.tool_name if n == 0 else None,
                    "args": part,
                    "id": "call_0" if n == 0 else None,
                    "index": 0,
                }
                out.append(AIMessageChunk(content="", tool_call_chunks=[call]))
            out.append(AIMessageChunk(content="", usage_metadata=usage))
            return out
        text = str(value.content) if isinstance(value, AIMessage) else str(value)
        parts = [text[i : i + self.piece] for i in range(0, len(text), self.piece)]
        out = [AIMessageChunk(content=part) for part in parts]
        out.append(AIMessageChunk(content="", usage_metadata=usage))
        return out

    async def _astream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **_: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        for message in self._chunks(messages):
            chunk = ChatGenerationChunk(message=message)
            if run_manager is not None:
                await run_manager.on_llm_new_token(str(message.content), chunk=chunk)
            yield chunk

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **_: Any
    ) -> ChatResult:
        total: AIMessageChunk | None = None
        for message in self._chunks(messages):
            total = message if total is None else total + message
        assert total is not None
        return ChatResult(generations=[ChatGeneration(message=total)])


@dataclass
class StreamingFakeLLM(FakeLLM):
    """FakeLLM whose models stream: same scripts, real chunks."""

    def model(self, role: str) -> Any:  # type: ignore[override]
        return StreamingFakeChat(llm=self, role=role)
