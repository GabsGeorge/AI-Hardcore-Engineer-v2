"""Owner reads their conversation and the final message after a human reply (REQ-API-05)."""

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from langchain_core.messages import AIMessage, HumanMessage
from sse_starlette.sse import EventSourceResponse

from app.api.auth import CurrentUser, current_user
from app.api.chat import graph_config
from app.api.schemas import Conversation, ConversationMessage, HandoffRef, HandoffStatus
from app.core.errors import AppError, not_found
from app.db import repo
from app.db.pool import get_pool

router = APIRouter()

# Limite de canais SSE simultâneos por usuário. O channel é um poll de ~60s; sem teto
# um cliente malicioso ou com bug pode acumular conexões e segurar workers do servidor.
MAX_SSE_PER_USER = int(os.getenv("SSE_EVENTS_MAX_PER_USER", "3"))
_sse_connections: dict[str, int] = defaultdict(int)
_sse_lock = asyncio.Lock()


async def _acquire_sse_slot(user_id: str) -> None:
    async with _sse_lock:
        if _sse_connections[user_id] >= MAX_SSE_PER_USER:
            raise AppError(
                429,
                "too_many_connections",
                f"Too many open event streams (max {MAX_SSE_PER_USER}).",
            )
        _sse_connections[user_id] += 1


async def _release_sse_slot(user_id: str) -> None:
    async with _sse_lock:
        remaining = _sse_connections[user_id] - 1
        if remaining <= 0:
            _sse_connections.pop(user_id, None)
        else:
            _sse_connections[user_id] = remaining


@dataclass(frozen=True)
class _ChannelState:
    """What the events channel reads from the checkpoint on each cycle."""

    ai: list[AIMessage]
    escalation: dict[str, Any]


def _final_message(snapshot: _ChannelState) -> dict[str, str]:
    """The latest answer. The id is the answer count, so the same answer sent again by a
    reopened channel keeps its id and the client drops the repeat (`openGetStream`)."""
    return {
        "event": "final_message",
        "id": f"final_message:{len(snapshot.ai)}",
        "data": json.dumps({"content": str(snapshot.ai[-1].content)}),
    }


def _reply_catch_up(snapshot: _ChannelState, ticket: str | None) -> bool:
    """True when the latest answer is the operator's reply to `ticket`, so the channel must send
    it although the answer count did not grow since the channel opened.

    The reply endpoint resumes the graph (which writes the reply) and only then resolves the
    ticket. A channel opened between the two, or after both, has the reply in its baseline: it
    used to send only `handoff_update: resolved`, or nothing, and the reply never reached the
    merchant. `turn_input` resets `escalation` at the start of every turn, so it names this
    ticket as resolved only while the resumed turn is still the conversation's latest one: a
    later, unrelated answer is never sent as the operator's reply."""
    return (
        bool(snapshot.ai)
        and ticket is not None
        and snapshot.escalation.get("ticket_id") == ticket
        and snapshot.escalation.get("status") == "resolved"
    )


@router.get("/v1/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: UUID, request: Request, user: CurrentUser = Depends(current_user)
) -> Conversation:
    handoff_row = await repo.get_conversation_handoff(get_pool(), user.id, conversation_id)
    if handoff_row is None:
        raise not_found()
    graph = request.app.state.graph
    if graph is None:
        raise not_found()
    state = await graph.aget_state(graph_config(user.id, conversation_id, request.state.trace_id))
    values: dict[str, Any] = state.values or {}
    turns = await repo.turns_by_message(
        get_pool(), user_id=user.id, conversation_id=conversation_id
    )
    messages: list[ConversationMessage] = []
    for m in values.get("messages") or []:
        if isinstance(m, HumanMessage):
            messages.append(ConversationMessage(role="user", content=str(m.content)))
        elif isinstance(m, AIMessage):
            turn = turns.get(m.id or "") or {}
            messages.append(
                ConversationMessage(
                    role="assistant",
                    content=str(m.content),
                    trace_id=turn.get("trace_id"),
                    sources=turn.get("sources") or [],
                )
            )
    status: HandoffStatus = handoff_row.get("handoff_status") or "none"
    return Conversation(
        conversation_id=conversation_id,
        handoff=HandoffRef(status=status, ticket_id=handoff_row.get("ticket_id")),
        last_activity_at=values.get("last_activity_at")
        or (await _last_activity(user.id, conversation_id)),
        messages=messages,
    )


async def _last_activity(user_id: str, conversation_id: UUID) -> Any:
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT last_activity_at FROM conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return row["last_activity_at"]


@router.get("/v1/conversations/{conversation_id}/events")
async def conversation_events(
    conversation_id: UUID, request: Request, user: CurrentUser = Depends(current_user)
) -> EventSourceResponse:
    """Canal SSE push para o dono: handoff_update e final_message (REQ-API-07)."""
    # Dono só: para terceiros devolvemos 404 antes de abrir o canal.
    row = await repo.get_conversation_handoff(get_pool(), user.id, conversation_id)
    if row is None:
        raise not_found()
    await _acquire_sse_slot(user.id)

    graph = request.app.state.graph
    config = graph_config(user.id, conversation_id, request.state.trace_id)

    async def _snapshot() -> _ChannelState | None:
        if graph is None:
            return None
        try:
            state = await graph.aget_state(config)
        except Exception:  # noqa: BLE001 — leitura best-effort; o próximo ciclo tenta de novo
            return None
        values: dict[str, Any] = state.values or {}
        return _ChannelState(
            ai=[m for m in values.get("messages") or [] if isinstance(m, AIMessage)],
            escalation=values.get("escalation") or {},
        )

    async def _generator() -> Any:
        """Cada evento leva um `id` estável, para o cliente descartar repetições quando o
        EventSource reconecta (o canal fecha a cada ~60 s e o navegador reabre sozinho)."""
        opened_status: str | None = row.get("handoff_status")
        # Canal aberto com o ticket já `resolved` chegou atrasado: o cliente só escuta em
        # `awaiting_human` e perdeu a transição. Sem status "anterior", o primeiro ciclo trata
        # o `resolved` como novo e entrega a resposta e o status (ver `_reply_catch_up`).
        last_status: str | None = None if opened_status == "resolved" else opened_status
        # Só mensagens NOVAS desde a abertura do canal: as respostas que a conversa já tinha
        # não podem voltar como se fossem a resposta do atendente.
        baseline = await _snapshot()
        last_ai_count = len(baseline.ai) if baseline is not None else 0
        replied_ticket: str | None = None  # ticket whose reply this channel already sent
        last_sla_announced: str | None = None  # ticket_id whose SLA breach we already told
        last_expired_announced: str | None = None
        try:
            for _ in range(60):  # até 60 ciclos de 1 s — cliente pode reabrir
                await asyncio.sleep(1)
                info = await repo.get_conversation_handoff(get_pool(), user.id, conversation_id)
                if info is None:
                    return
                # Postgres returns the ticket id as a UUID; json.dumps cannot encode it, and
                # the TypeError ended the stream right after final_message (QA-023).
                ticket = str(info["ticket_id"]) if info.get("ticket_id") else None
                current_status = info.get("handoff_status")
                # Lida DEPOIS do status: o reply grava a mensagem antes de resolver o ticket,
                # então um `resolved` lido acima garante que a resposta já está aqui.
                snapshot = await _snapshot()
                # final_message ANTES de handoff_update: o reply do operador grava a mensagem e
                # depois resolve o ticket; se o cliente visse `resolved` primeiro, fecharia o
                # canal antes de receber a resposta.
                if snapshot is not None and len(snapshot.ai) > last_ai_count:
                    last_ai_count = len(snapshot.ai)
                    replied_ticket = ticket
                    yield _final_message(snapshot)
                if current_status != last_status:
                    if current_status == "resolved" and replied_ticket != ticket:
                        if snapshot is None and graph is not None:
                            # Sem ler a conversa não dá para mandar a resposta antes do status:
                            # segura o `resolved` e tenta de novo no próximo ciclo.
                            continue
                        if snapshot is not None and _reply_catch_up(snapshot, ticket):
                            last_ai_count = len(snapshot.ai)
                            replied_ticket = ticket
                            yield _final_message(snapshot)
                    last_status = current_status
                    yield {
                        "event": "handoff_update",
                        "id": f"handoff_update:{ticket}:{current_status or 'none'}",
                        "data": json.dumps(
                            {"status": current_status or "none", "ticket_id": ticket}
                        ),
                    }
                # sla_breached + handoff_expired come from the live handoffs row — the scheduler
                # updates priority/status there, so a single DB probe tells us when to fire.
                open_row = await repo.get_latest_handoff_for_conversation(
                    get_pool(), user.id, conversation_id
                )
                if open_row is not None:
                    ticket_id = str(open_row["id"])
                    if (
                        open_row["status"] == "awaiting_human"
                        and open_row["priority"] == "high"
                        and last_sla_announced != ticket_id
                    ):
                        last_sla_announced = ticket_id
                        yield {
                            "event": "sla_breached",
                            "id": f"sla_breached:{ticket_id}",
                            "data": json.dumps(
                                {"ticket_id": ticket_id, "breached_at": info.get("ts", "")}
                            ),
                        }
                    if open_row["status"] == "expired" and last_expired_announced != ticket_id:
                        last_expired_announced = ticket_id
                        yield {
                            "event": "handoff_expired",
                            "id": f"handoff_expired:{ticket_id}",
                            "data": json.dumps({"ticket_id": ticket_id}),
                        }
        finally:
            await _release_sse_slot(user.id)

    return EventSourceResponse(_generator())
