"""LLM roteirizado para o E2E do navegador (ADR 0006), por papel e palavra-chave.

Diferente do `FakeLLM` dos testes de integração (fila de respostas por teste), o E2E roda contra
uma api de pé que atende vários turnos: a resposta precisa ser decidida pela mensagem. Só o modelo
é simulado; grafo, guardrails, HITL, banco e SSE são os reais.

- pedido de atendente/humano → input_guard `human_request` e supervisor roteia para `escalation`;
- qualquer outra pergunta → `knowledge`, que com a base vazia responde "não encontrei".
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

USAGE = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}
_HUMAN = re.compile(r"\b(atendente|humano|human|agent|pessoa)\b", re.IGNORECASE)


def _question(payload: Any) -> str:
    """Última mensagem humana; o supervisor recebe a conversa inteira."""
    if isinstance(payload, list):
        humans = [m for m in payload if isinstance(m, HumanMessage)]
        if humans:
            return str(humans[-1].content)
        return " ".join(str(getattr(m, "content", m)) for m in payload)
    if isinstance(payload, BaseMessage):
        return str(payload.content)
    return str(payload)


def _structured(role: str, question: str) -> dict[str, Any]:
    wants_human = bool(_HUMAN.search(question))
    if role == "input_guard":
        return {"classification": "human_request" if wants_human else "safe"}
    if role == "supervisor":
        agent = "escalation" if wants_human else "knowledge"
        return {
            "route": [agent],
            "subtasks": [{"id": "s1", "agent": agent, "query": question[:500] or "pergunta"}],
            "confidence": 0.9,
            "rationale": "e2e",
            "language": "pt",
            "sensitive": False,
        }
    if role == "knowledge_rewrite":
        return {"variants": [], "general": False}
    if role == "knowledge":
        return {"answer": "Resposta de teste.", "confidence": 0.9, "cited": [1]}
    if role == "output_guard":
        return {"grounded": True, "reply_supported": True, "about_other_thing": False}
    if role == "online_eval":
        return {"faithfulness": 1.0, "relevance": 1.0}
    raise ValueError(f"papel sem saída estruturada roteirizada: {role}")


class E2EChatModel:
    def __init__(self, role: str) -> None:
        self.role = role

    async def ainvoke(self, payload: Any, config: Any = None, **_: Any) -> AIMessage:
        if self.role == "escalation":
            text = "Lojista pediu para falar com um atendente humano."
        elif self.role == "summarize":
            text = "Resumo da conversa."
        else:
            text = "Resposta de teste."
        return AIMessage(content=text, usage_metadata=USAGE)

    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool = False, **_: Any
    ) -> _Structured:
        return _Structured(self.role, schema, include_raw)

    def bind_tools(self, *_: Any, **__: Any) -> E2EChatModel:
        return self


class _Structured:
    def __init__(self, role: str, schema: type[BaseModel], include_raw: bool) -> None:
        self.role, self.schema, self.include_raw = role, schema, include_raw

    async def ainvoke(self, payload: Any, config: Any = None, **_: Any) -> Any:
        parsed = self.schema.model_validate(_structured(self.role, _question(payload)))
        if self.include_raw:
            raw = AIMessage(content="", usage_metadata=USAGE)
            return {"raw": raw, "parsed": parsed, "parsing_error": None}
        return parsed


def install() -> None:
    from app.core import llm

    llm.set_overrides(model_factory=E2EChatModel, embeddings=DeterministicFakeEmbedding(size=1536))
