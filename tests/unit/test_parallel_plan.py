"""Supervisor plan in parallel with the input classifier (REQ-REL-04, latency step 1).

The plan does not depend on the classifier's verdict, so input_guardrail starts it at the same
time and the supervisor adopts it. A blocked turn throws the plan away; a long thread (about to be
summarized) plans sequentially as before; the planner only ever sees the masked text.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.memory import MEMORY_MAX_MESSAGES
from app.graph.supervisor import supervisor
from app.guardrails.input import input_guardrail
from tests.fakes import FakeLLM, Scripted, make_plan

SAFE = {"classification": "safe", "reason": "getnet question"}


@pytest.mark.req("REQ-REL-04")
async def test_safe_turn_carries_the_plan_and_its_tokens(fake_llm: FakeLLM) -> None:
    fake_llm.script(input_guard=[SAFE], supervisor=[Scripted(make_plan("support"), tokens=321)])
    msg = HumanMessage(content="Meu CPF é 111.111.111-11, quando cai meu dinheiro?", id="m1")
    update = await input_guardrail({"messages": [msg]})
    assert "input:blocked" not in update["guardrail_flags"]
    assert update["preplan"]["plan"]["route"] == ["support"]
    assert update["preplan"]["tokens"] == 321
    assert "tokens_used" not in update  # counted by the supervisor when it adopts the plan
    planner_prompt = fake_llm.prompts("supervisor")
    assert "[CPF]" in planner_prompt and "111.111.111-11" not in planner_prompt


@pytest.mark.req("REQ-REL-04", "REQ-GR-01")
async def test_blocked_turn_has_no_plan(fake_llm: FakeLLM) -> None:
    fake_llm.script(
        input_guard=[{"classification": "out_of_scope", "reason": "sports"}],
        supervisor=[make_plan("knowledge")],
    )
    update = await input_guardrail({"messages": [HumanMessage(content="Quem ganhou o jogo?")]})
    assert update["guardrail_flags"] == ["input:blocked"]
    assert update["preplan"] is None


@pytest.mark.req("REQ-REL-04")
async def test_long_thread_plans_after_summarize(fake_llm: FakeLLM) -> None:
    fake_llm.script(input_guard=[SAFE])
    history: list[Any] = []
    for i in range(MEMORY_MAX_MESSAGES // 2 + 1):
        history += [HumanMessage(content=f"pergunta {i}"), AIMessage(content=f"resposta {i}")]
    history.append(HumanMessage(content="E a taxa do débito?"))
    update = await input_guardrail({"messages": history})
    assert update["preplan"] is None
    assert fake_llm.prompts("supervisor") == ""


@pytest.mark.req("REQ-REL-04", "REQ-ORC-01")
async def test_supervisor_adopts_the_preplan_without_planning_again(fake_llm: FakeLLM) -> None:
    # No supervisor reply is scripted: planning again would fail and degrade the turn.
    state = {
        "messages": [HumanMessage(content="Quando cai meu dinheiro?")],
        "plan": None,
        "preplan": {"plan": make_plan("support"), "tokens": 321},
        "step": 0,
        "attempts": 0,
        "tokens_used": 0,
        "results": [],
    }
    command = await supervisor(state, {"configurable": {}})
    assert command.update["plan"]["route"] == ["support"]
    assert command.update["tokens_used"] == 321
    assert command.goto != "degraded_response"
    assert fake_llm.prompts("supervisor") == ""
