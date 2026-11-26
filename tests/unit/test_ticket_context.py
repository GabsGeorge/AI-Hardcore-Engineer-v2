"""O contexto do chamado diz quem é o lojista e o que ele disse (QA-022, contrato Handoff)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents import escalation


@pytest.mark.req("REQ-HITL-01")
async def test_context_carries_merchant_recent_messages_route_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def merchant_name(_pool: Any, user_id: str) -> str | None:
        return "Mercadinho Sol Nascente" if user_id == "cliente1988" else None

    monkeypatch.setattr(escalation.repo, "get_merchant_name", merchant_name)
    monkeypatch.setattr(escalation, "get_pool", lambda: None)
    history: list[Any] = [SystemMessage(content="regras")]
    for i in range(5):
        history += [HumanMessage(content=f"pergunta {i}"), AIMessage(content=f"resposta {i}")]
    history.append(HumanMessage(content="x" * 900))
    state: dict[str, Any] = {
        "messages": history,
        "route_trace": ["input_guardrail", "supervisor", "escalation"],
        "results": [{"tools_used": [{"name": "diagnose_terminal", "status": "ok"}]}],
    }

    ctx = await escalation.ticket_context(state, "cliente1988")

    assert ctx["merchant"] == {"id": "cliente1988", "name": "Mercadinho Sol Nascente"}
    recent = ctx["recent_messages"]
    assert len(recent) == 6
    assert recent[-1] == {"role": "merchant", "content": "x" * 500}
    assert {"role": "assistant", "content": "resposta 4"} in recent
    assert all(m["role"] in ("merchant", "assistant") for m in recent)
    assert ctx["route"] == ["input_guardrail", "supervisor", "escalation"]
    assert ctx["tools_used"] == [{"name": "diagnose_terminal", "status": "ok"}]
