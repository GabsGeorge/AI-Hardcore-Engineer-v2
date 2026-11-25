"""Owner reads their conversation and the final message after a human reply (REQ-API-05)."""

import asyncio
import json
import os
from collections import defaultdict
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
    messages: list[ConversationMessage] = []
    for m in values.get("messages") or []:
        if isinstance(m, HumanMessage):
            messages.append(ConversationMessage(role="user", content=str(m.content)))
        elif isinstance(m, AIMessage):
            messages.append(ConversationMessage(role="assistant", content=str(m.content)))
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

    async def _ai_messages() -> list[AIMessage] | None:
        if graph is None:
            return None
        try:
            state = await graph.aget_state(config)
        except Exception:  # noqa: BLE001 — leitura best-effort; o próximo ciclo tenta de novo
            return None
        return [m for m in (state.values or {}).get("messages") or [] if isinstance(m, AIMessage)]

    async def _generator() -> Any:
        """Cada evento leva um `id` estável, para o cliente descartar repetições quando o
        EventSource reconecta (o canal fecha a cada ~60 s e o navegador reabre sozinho)."""
        last_status: str | None = row.get("handoff_status")
        # Só mensagens NOVAS desde a abertura do canal: as respostas que a conversa já tinha
        # não podem voltar como se fossem a resposta do atendente.
        baseline = await _ai_messages()
        last_ai_count = len(baseline) if baseline is not None else 0
        last_sla_announced: str | None = None  # ticket_id whose SLA breach we already told
        last_expired_announced: str | None = None
        try:
            for _ in range(60):  # até 60 ciclos de 1 s — cliente pode reabrir
                await asyncio.sleep(1)
                info = await repo.get_conversation_handoff(get_pool(), user.id, conversation_id)
                if info is None:
                    return
                # final_message ANTES de handoff_update: o reply do operador grava a mensagem e
                # depois resolve o ticket; se o cliente visse `resolved` primeiro, fecharia o
                # canal antes de receber a resposta.
                ai_messages = await _ai_messages()
                if ai_messages is not None and len(ai_messages) > last_ai_count:
                    last_ai_count = len(ai_messages)
                    yield {
                        "event": "final_message",
                        "id": f"final_message:{last_ai_count}",
                        "data": json.dumps({"content": str(ai_messages[-1].content)}),
                    }
                current_status = info.get("handoff_status")
                if current_status != last_status:
                    last_status = current_status
                    ticket = info.get("ticket_id")
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
