"""Pergunta composta → árvore de spans completa, com pai e filho corretos (REQ-OBS-02).

Um ``InMemorySpanExporter`` é instalado no provider do OTel e a pergunta composta (Knowledge +
Support) passa pela API real com o LLM roteirizado (ADR 0006). O teste checa a árvore inteira:

    http.request
    └─ fastapi.endpoint  (emitido pelo próprio FastAPI; os nós abaixo são filhos dele)
    ├─ input_guardrail ── llm.input_guard
    ├─ supervisor ── supervisor.plan ── llm.supervisor
    ├─ knowledge ── llm.knowledge_rewrite
    │            ├─ tool.search_knowledge_base ── retrieval.search
    │            │                                ├─ retrieval.bm25
    │            │                                ├─ retrieval.dense ── retrieval.embedding
    │            │                                └─ retrieval.rerank
    │            └─ llm.knowledge
    ├─ support ── llm.support (x2), tool.get_settlement_schedule
    ├─ supervisor  (segunda passada: decide → synthesize, sem LLM)
    ├─ synthesize ── llm.synthesize
    └─ output_guardrail ── llm.output_guard
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.db.migrate import apply_seed
from app.db.pool import Pool
from tests.fakes import FakeLLM
from tests.fixtures.kb import answer, fresh_retriever, rewrite, seed_kb

__all__ = ["fresh_retriever"]
pytestmark = [pytest.mark.req("REQ-OBS-02"), pytest.mark.usefixtures("fresh_retriever")]

KNOWLEDGE_Q = "Get Smart maquininha inteligente"
SUPPORT_Q = "Quando cai o dinheiro das vendas de ontem?"
USAGE = {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100}


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), "o lifespan da app configura o SDK provider"
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()  # para de exportar para este exporter depois do teste
    exporter.clear()


def _plan() -> dict[str, Any]:
    return {
        "route": ["knowledge", "support"],
        "subtasks": [
            {"id": "s1", "agent": "knowledge", "query": KNOWLEDGE_Q},
            {"id": "s2", "agent": "support", "query": SUPPORT_Q},
        ],
        "confidence": 0.9,
        "rationale": "pergunta composta",
        "language": "pt",
        "sensitive": False,
    }


def _tool_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_settlement_schedule",
                "args": {"from_date": "2026-10-13", "to_date": "2026-10-13"},
                "id": "call_0",
            }
        ],
        usage_metadata=USAGE,
    )


class Tree:
    """Spans of one trace indexed by name and id, for parent/child assertions."""

    def __init__(self, spans: list[ReadableSpan], trace_id: int) -> None:
        self.spans = [s for s in spans if s.context.trace_id == trace_id]
        self.by_id = {s.context.span_id: s for s in self.spans}

    def named(self, name: str) -> list[ReadableSpan]:
        return [s for s in self.spans if s.name == name]

    def one(self, name: str) -> ReadableSpan:
        found = self.named(name)
        assert len(found) == 1, f"esperava 1 span {name!r}, achei {len(found)}"
        return found[0]

    def parent(self, span: ReadableSpan) -> ReadableSpan:
        assert span.parent is not None, f"{span.name} não tem pai"
        parent = self.by_id.get(span.parent.span_id)
        assert parent is not None, f"pai de {span.name} fora do trace"
        return parent

    def assert_child(self, child: str, parent: str) -> None:
        for span in self.named(child):
            assert self.parent(span).name == parent, (
                f"{child} deveria ser filho de {parent}, é de {self.parent(span).name}"
            )
        assert self.named(child), f"span {child!r} ausente"


async def test_compound_question_produces_full_span_tree(
    client: httpx.AsyncClient,
    fake_llm: FakeLLM,
    db_pool: Pool,
    spans: InMemorySpanExporter,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    await apply_seed(db_pool, date(2026, 10, 13))
    await seed_kb(db_pool)
    fake_llm.script(
        input_guard=[{"classification": "safe"}],
        supervisor=[_plan()],
        knowledge_rewrite=[rewrite()],
        knowledge=[answer("A Get Smart é a maquininha inteligente da Getnet [1].", 0.9, (1,))],
        support=[_tool_call(), AIMessage(content="Cai R$ 460,65 em 13/10.", usage_metadata=USAGE)],
        synthesize=["A Get Smart é a maquininha inteligente; e R$ 460,65 cai em 13/10."],
        output_guard=[{"grounded": True, "reply_supported": True, "about_other_thing": False}],
    )

    resp = await client.post(
        "/v1/chat",
        json={"message": f"{KNOWLEDGE_Q}? E {SUPPORT_Q}"},
        headers=auth_headers["cliente1988"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"knowledge", "support"} <= set(body["route"]), body["route"]

    tree = Tree(list(spans.get_finished_spans()), int(body["trace_id"], 16))
    root = tree.one("http.request")
    assert root.parent is None, "http.request é a raiz do trace"
    assert root.attributes["http.route"] == "/v1/chat"

    # FastAPI (>= 0.13x) abre o próprio span `fastapi.endpoint` em volta do handler; os nós do
    # grafo ficam abaixo dele. Sem esse span, eles seriam filhos diretos de http.request.
    endpoint = tree.parent(tree.one("input_guardrail"))
    if endpoint.name == "fastapi.endpoint":
        assert tree.parent(endpoint).name == "http.request"
    holder = endpoint.name
    assert holder in ("fastapi.endpoint", "http.request")

    # Nós do grafo: todos irmãos, filhos do span do endpoint.
    for node in ("input_guardrail", "knowledge", "support", "synthesize", "output_guardrail"):
        tree.assert_child(node, holder)
        assert tree.one(node).attributes["graph.node"] == node
    assert len(tree.named("supervisor")) == 2, "plan + decide"
    tree.assert_child("supervisor", holder)

    # LLM: um span por chamada, filho do nó que a fez.
    tree.assert_child("llm.input_guard", "input_guardrail")
    tree.assert_child("supervisor.plan", "supervisor")
    tree.assert_child("llm.supervisor", "supervisor.plan")
    tree.assert_child("llm.knowledge_rewrite", "knowledge")
    tree.assert_child("llm.knowledge", "knowledge")
    tree.assert_child("llm.support", "support")
    assert len(tree.named("llm.support")) == 2, "tool round + resposta"
    tree.assert_child("llm.synthesize", "synthesize")
    tree.assert_child("llm.output_guard", "output_guardrail")

    # Tools (via call_tool) e retrieval.
    tree.assert_child("tool.search_knowledge_base", "knowledge")
    tree.assert_child("tool.get_settlement_schedule", "support")
    tree.assert_child("retrieval.search", "tool.search_knowledge_base")
    tree.assert_child("retrieval.bm25", "retrieval.search")
    tree.assert_child("retrieval.dense", "retrieval.search")
    tree.assert_child("retrieval.embedding", "retrieval.dense")
    tree.assert_child("retrieval.rerank", "retrieval.search")

    # Atributos do span de LLM: papel, modelo, tokens, latência e versão do prompt.
    llm = tree.one("llm.supervisor").attributes
    assert llm["llm.role"] == "supervisor"
    assert llm["gen_ai.request.model"]
    assert llm["gen_ai.usage.input_tokens"] == 100
    assert llm["gen_ai.usage.output_tokens"] == 20
    assert llm["llm.latency_ms"] >= 0
    assert llm["prompt.name"] == "supervisor"
    assert isinstance(llm["prompt.version"], str) and len(llm["prompt.version"]) == 12
    assert tree.one("llm.output_guard").attributes["prompt.name"] == "grounding"

    tool = tree.one("tool.get_settlement_schedule").attributes
    assert (tool["tool.agent"], tool["tool.status"]) == ("support", "ok")
    search = tree.one("retrieval.search").attributes
    assert search["retrieval.strategy"] == "hybrid_rerank"
    assert search["retrieval.hits"] >= 1
    assert tree.one("retrieval.rerank").attributes["rerank.outcome"] == "ok"
