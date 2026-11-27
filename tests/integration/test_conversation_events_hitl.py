"""The operator's reply reaches the merchant over `/v1/conversations/{id}/events` whenever the
channel opens (REQ-API-07, REQ-HITL-03).

The reply endpoint resumes the graph, which writes the reply as an AIMessage, and only then
resolves the ticket. The channel used to take the answers present at open as a baseline and send
`final_message` only for answers added later, so a channel opened between those two writes, or
after both, never sent the reply: the merchant saw `respondido` with no answer, or nothing at all.
That is how `apps/web/e2e/07-hitl-realtime.spec.ts` failed in CI (runs 45924616525, 46070852196).

The test HTTP client buffers an EventSourceResponse until it ends (~60 s), so the generator is
driven directly, with the real app, Postgres and graph. Its 1 s pause is replaced by a hook that
runs each test's step between cycles, which pins the interleaving the race needs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from langgraph.types import Command

from app.api import conversations as conv_mod
from app.api.auth import CurrentUser, create_token
from app.api.chat import graph_config
from app.db import repo
from app.db.pool import Pool
from tests.fakes import FakeLLM, make_plan

MERCHANT = "cliente1988"
REPLY = "Estorno aprovado para a venda tx-123."
Step = Callable[[], Awaitable[Any]]


@pytest.fixture
def operator_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token('operador01', 'operator')}"}


class _Cycles:
    """Stands in for the `asyncio` module inside `app.api.conversations`: `sleep` (the pause
    before each cycle) runs the step planned for that cycle instead of waiting."""

    def __init__(self) -> None:
        self.cycle = 0
        self.steps: dict[int, Step] = {}

    async def sleep(self, _: float) -> None:
        self.cycle += 1
        step = self.steps.get(self.cycle)
        if step is not None:
            await step()

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


@pytest.fixture
def cycles(monkeypatch: pytest.MonkeyPatch) -> _Cycles:
    fake = _Cycles()
    monkeypatch.setattr(conv_mod, "asyncio", fake)
    conv_mod._sse_connections.clear()
    return fake


async def _escalate(client: httpx.AsyncClient, fake_llm: FakeLLM, headers: dict[str, str]) -> Any:
    fake_llm.script(
        supervisor=[make_plan("escalation", query="quero um humano")],
        escalation=["Lojista pede atendente."],
    )
    resp = await client.post(
        "/v1/chat", json={"message": "quero falar com um atendente"}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["handoff"]["status"] == "awaiting_human"
    return body


async def _channel(app: FastAPI, conversation_id: str) -> list[dict[str, Any]]:
    """Opens the events channel as the merchant and collects every event of its ~60 cycles."""
    request = SimpleNamespace(app=app, state=SimpleNamespace(trace_id="0" * 32))
    response = await conv_mod.conversation_events(
        UUID(conversation_id), request, CurrentUser(id=MERCHANT, role="merchant")
    )
    return [event async for event in response.body_iterator]  # type: ignore[attr-defined]


def _summary(events: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    return [(e["event"], json.loads(e["data"])) for e in events]


async def _resume_only(app: FastAPI, conversation_id: str) -> None:
    """First half of the reply endpoint: the graph writes the reply, the ticket stays open."""
    await app.state.graph.ainvoke(
        Command(resume={"reply": REPLY, "action_decision": None}),
        config=graph_config(MERCHANT, conversation_id, "0" * 32),
    )


@pytest.mark.req("REQ-API-07", "REQ-HITL-03")
async def test_reply_written_before_the_channel_opens_arrives_before_resolved(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake_llm: FakeLLM,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
    cycles: _Cycles,
) -> None:
    """Failure 1: the channel opens after the reply is written, while the ticket is still
    `awaiting_human`. It used to send only `handoff_update: resolved`."""
    first = await _escalate(client, fake_llm, auth_headers[MERCHANT])
    conversation_id, ticket_id = first["conversation_id"], first["handoff"]["ticket_id"]
    await _resume_only(app, conversation_id)

    async def resolve() -> None:
        await repo.resolve_handoff(
            db_pool,
            ticket_id=UUID(ticket_id),
            operator_id="operador01",
            reply=REPLY,
            action_decision=None,
        )

    cycles.steps[2] = resolve
    events = await _channel(app, conversation_id)

    assert [name for name, _ in _summary(events)] == ["final_message", "handoff_update"]
    final, update = _summary(events)
    assert REPLY in final[1]["content"]
    assert update[1] == {"status": "resolved", "ticket_id": ticket_id}


@pytest.mark.req("REQ-API-07", "REQ-HITL-03")
async def test_channel_opened_after_resolution_still_delivers_the_reply(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake_llm: FakeLLM,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
    operator_headers: dict[str, str],
    cycles: _Cycles,
) -> None:
    """Failure 2: the ticket is already resolved when the channel opens. It used to send
    nothing, and the merchant's screen stayed on `aguardando atendente`."""
    first = await _escalate(client, fake_llm, auth_headers[MERCHANT])
    conversation_id, ticket_id = first["conversation_id"], first["handoff"]["ticket_id"]
    reply = await client.post(
        f"/v1/handoffs/{ticket_id}/reply", json={"reply": REPLY}, headers=operator_headers
    )
    assert reply.status_code == 200

    events = await _channel(app, conversation_id)

    assert [name for name, _ in _summary(events)] == ["final_message", "handoff_update"]
    final, update = _summary(events)
    assert REPLY in final[1]["content"]
    assert update[1] == {"status": "resolved", "ticket_id": ticket_id}
    assert events[1]["id"] == f"handoff_update:{ticket_id}:resolved"


@pytest.mark.req("REQ-API-07", "REQ-HITL-03")
async def test_reply_while_the_channel_is_open_is_unchanged_and_a_reopen_reuses_the_ids(
    app: FastAPI,
    client: httpx.AsyncClient,
    fake_llm: FakeLLM,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
    operator_headers: dict[str, str],
    cycles: _Cycles,
) -> None:
    """The normal path: the channel is open while awaiting, the operator replies, and the reply
    arrives once, before the status. A channel the browser reopens afterwards sends the same
    events with the same ids, which the client's de-duplication drops (`openGetStream`)."""
    first = await _escalate(client, fake_llm, auth_headers[MERCHANT])
    conversation_id, ticket_id = first["conversation_id"], first["handoff"]["ticket_id"]

    async def operator_replies() -> None:
        resp = await client.post(
            f"/v1/handoffs/{ticket_id}/reply", json={"reply": REPLY}, headers=operator_headers
        )
        assert resp.status_code == 200

    cycles.steps[3] = operator_replies
    events = await _channel(app, conversation_id)

    assert [name for name, _ in _summary(events)] == ["final_message", "handoff_update"]
    final, update = _summary(events)
    assert REPLY in final[1]["content"]
    assert update[1] == {"status": "resolved", "ticket_id": ticket_id}

    cycles.steps.clear()
    reopened = await _channel(app, conversation_id)
    assert [e["id"] for e in reopened] == [e["id"] for e in events]
