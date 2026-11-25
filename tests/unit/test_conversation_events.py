"""Canal /v1/conversations/{id}/events (FR-010, T065): o que o lojista recebe enquanto espera.

O cliente HTTP de teste bufferiza o EventSourceResponse, então o gerador é exercitado direto, com
o repositório e o grafo simulados e o `sleep` de 1 s zerado.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.api import conversations as conv_mod
from app.api.auth import CurrentUser

TICKET = "7d3c2b1a-0000-4000-8000-000000000001"


class Script:
    """Estado por ciclo: (status do handoff na conversa, linha do handoff, nº de respostas)."""

    def __init__(self, cycles: list[tuple[str, dict[str, Any] | None, int]]) -> None:
        self.cycles = cycles
        self.cycle = -1  # -1 = leitura feita antes do loop (abertura do canal)

    def current(self) -> tuple[str, dict[str, Any] | None, int]:
        return self.cycles[max(self.cycle, 0)]


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Iterator[Script]:
    holder: dict[str, Script] = {}

    async def no_sleep(_: float) -> None:
        holder["s"].cycle += 1

    async def conversation_handoff(*_: Any) -> dict[str, Any] | None:
        s = holder["s"]
        if s.cycle >= len(s.cycles):
            return None  # conversa sumiu: o gerador termina
        status, _, _ = s.current()
        return {"handoff_status": status, "ticket_id": TICKET, "ts": ""}

    async def latest_handoff(*_: Any) -> dict[str, Any] | None:
        return holder["s"].current()[1]

    monkeypatch.setattr(conv_mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(conv_mod.repo, "get_conversation_handoff", conversation_handoff)
    monkeypatch.setattr(conv_mod.repo, "get_latest_handoff_for_conversation", latest_handoff)
    monkeypatch.setattr(conv_mod, "get_pool", lambda: None)
    conv_mod._sse_connections.clear()

    def install(cycles: list[tuple[str, dict[str, Any] | None, int]]) -> Script:
        holder["s"] = Script(cycles)
        return holder["s"]

    yield install  # type: ignore[misc]


def _graph(s: Script) -> Any:
    async def aget_state(_: Any) -> Any:
        answers = s.current()[2]
        messages: list[Any] = [HumanMessage(content="quero um atendente")]
        messages += [AIMessage(content=f"resposta {i}") for i in range(1, answers + 1)]
        return SimpleNamespace(values={"messages": messages})

    return SimpleNamespace(aget_state=aget_state)


async def _events(s: Script) -> list[dict[str, Any]]:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(graph=_graph(s))),
        state=SimpleNamespace(trace_id="0" * 32),
    )
    response = await conv_mod.conversation_events(
        uuid4(),
        request,
        CurrentUser(id="cliente1988", role="merchant"),  # type: ignore[arg-type]
    )
    return [event async for event in response.body_iterator]  # type: ignore[union-attr]


def _awaiting(priority: str = "normal") -> dict[str, Any]:
    return {"id": TICKET, "status": "awaiting_human", "priority": priority}


@pytest.mark.req("REQ-API-07")
async def test_old_answers_are_not_replayed_when_the_channel_opens(script: Any) -> None:
    s = script([("awaiting_human", _awaiting(), 1), ("awaiting_human", _awaiting(), 1)])
    assert await _events(s) == []


@pytest.mark.req("REQ-API-07", "REQ-HITL-03")
async def test_operator_reply_arrives_before_the_resolved_status(script: Any) -> None:
    resolved = {"id": TICKET, "status": "resolved", "priority": "normal"}
    s = script([("awaiting_human", _awaiting(), 1), ("resolved", resolved, 2)])
    events = await _events(s)
    assert [e["event"] for e in events] == ["final_message", "handoff_update"]
    assert json.loads(events[0]["data"]) == {"content": "resposta 2"}
    assert json.loads(events[1]["data"]) == {"status": "resolved", "ticket_id": TICKET}
    assert events[0]["id"] == "final_message:2"
    assert events[1]["id"] == f"handoff_update:{TICKET}:resolved"


@pytest.mark.req("REQ-API-07", "REQ-HITL-04")
async def test_sla_and_expiry_are_announced_once_each(script: Any) -> None:
    expired = {"id": TICKET, "status": "expired", "priority": "high"}
    s = script(
        [
            ("awaiting_human", _awaiting("high"), 1),
            ("awaiting_human", _awaiting("high"), 1),
            ("expired", expired, 1),
            ("expired", expired, 1),
        ]
    )
    names = [e["event"] for e in await _events(s)]
    assert names == ["sla_breached", "handoff_update", "handoff_expired"]
