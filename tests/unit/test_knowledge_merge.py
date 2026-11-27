"""Multi-query merge in the knowledge agent (REQ-RAG-05): every query variant contributes its
best passage before the rest is filled by score (golden sc-02: the Pix pages pushed out the MDR
page that the `taxa por transação` variant had found)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from app.agents.knowledge import merge_hits
from app.core.config import get_settings
from app.core.llm import get_model
from app.rag.retriever import KbHit
from tests.fixtures.kb import answer, rewrite


def _hit(chunk_id: int, score: float) -> KbHit:
    return KbHit(
        chunk_id=chunk_id,
        url=f"https://example.com/{chunk_id}",
        title=f"t{chunk_id}",
        section="",
        content="c",
        score=score,
        fetched_at=datetime(2026, 11, 26, tzinfo=UTC),
    )


@pytest.mark.req("REQ-RAG-05")
def test_each_variant_keeps_its_best_passage() -> None:
    pix = [_hit(i, 0.95 - i / 100) for i in range(1, 7)]  # six strong Pix chunks
    mdr = [_hit(100, 0.80)]  # the only hit of the fee variant
    merged = merge_hits([pix, mdr], k=5)
    assert 100 in {h.chunk_id for h in merged}
    assert len(merged) == 5
    assert [h.score for h in merged] == sorted((h.score for h in merged), reverse=True)


@pytest.mark.req("REQ-RAG-05")
def test_duplicates_keep_the_best_score_and_count_once() -> None:
    merged = merge_hits([[_hit(1, 0.5), _hit(2, 0.4)], [_hit(1, 0.9)]], k=5)
    assert [(h.chunk_id, h.score) for h in merged] == [(1, 0.9), (2, 0.4)]


@pytest.mark.req("REQ-RAG-05")
def test_empty_variants_are_ignored() -> None:
    assert merge_hits([[], [_hit(3, 0.7)], []]) == [_hit(3, 0.7)]


@pytest.mark.req("REQ-RAG-05")
def test_planner_knowledge_and_verifier_run_at_temperature_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "openai_api_key", SecretStr("unused-test-key"))  # build only, no calls
    monkeypatch.setattr(s, "openai_model_primary", "primary-model")
    monkeypatch.setattr(s, "openai_model_fallback", "")
    for role in ("supervisor", "knowledge_rewrite", "knowledge", "output_guard"):
        assert get_model(role).inner.temperature == 0.0, role


@pytest.mark.req("REQ-RAG-05", "REQ-REL-04")
async def test_planned_search_queries_skip_the_rewrite_call(
    fake_llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supervisor's plan carries the search queries: the specialist searches with them and
    does not call `knowledge_rewrite` (one model round trip less per knowledge turn)."""
    from app.agents import knowledge

    seen: list[list[tuple[str, str | None]]] = []

    async def fake_search(queries: Any, config: Any, prefetched: Any = None) -> Any:
        seen.append(list(queries))
        return [_hit(7, 0.9)], [], True

    monkeypatch.setattr(knowledge, "_search", fake_search)
    fake_llm.script(knowledge=[answer("A antecipação adianta recebíveis [1].", 0.9, (1,))])
    subtask = {
        "id": "s1",
        "agent": "knowledge",
        "query": "Como funciona a antecipação?",
        "search_queries": [
            {"query": "antecipação de recebíveis Getnet", "lang": "pt"},
            {"query": "receivables advance Getnet", "lang": "en"},
        ],
    }
    out = await knowledge.knowledge_node({"subtask": subtask, "language": "pt"}, {})
    assert fake_llm.prompts("knowledge_rewrite") == ""
    assert ("receivables advance Getnet", "en") in seen[0]
    assert out["results"][0]["confidence"] > 0


@pytest.mark.req("REQ-RAG-05")
@pytest.mark.parametrize(
    "question",
    ["Quais as taxas do Pix para pessoa jurídica?", "How much is the debit card fee?"],
)
async def test_fee_questions_keep_the_rewrite_even_with_planned_queries(
    fake_llm: Any, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    """The planner's queries for "taxas do Pix" never named the per-transaction fee (MDR page),
    and the answer said Pix had no per-transaction cost. Fee questions use the rewrite, whose
    prompt always adds the `taxa por transação MDR Getnet` query."""
    from app.agents import knowledge

    calls: list[list[tuple[str, str | None]]] = []

    async def fake_each(queries: Any, config: Any) -> Any:
        calls.append(list(queries))
        return [([_hit(7, 0.9)], {"name": "search_knowledge_base", "status": "ok"})]

    monkeypatch.setattr(knowledge, "_search_each", fake_each)
    fake_llm.script(
        knowledge_rewrite=[rewrite(("Pix taxa por transação MDR Getnet", "pt"))],
        knowledge=[answer("O MDR vale também para o Pix [1].", 0.9, (1,))],
    )
    subtask = {
        "id": "s1",
        "agent": "knowledge",
        "query": question,
        "search_queries": [{"query": "taxas Pix Getnet", "lang": "pt"}],
    }
    await knowledge.knowledge_node({"subtask": subtask, "language": "pt"}, {})
    assert fake_llm.prompts("knowledge_rewrite") != ""
    searched = [q for call in calls for q, _ in call]
    assert "Pix taxa por transação MDR Getnet" in searched
    assert "taxas Pix Getnet" not in searched


@pytest.mark.req("REQ-RAG-05")
async def test_without_planned_queries_the_specialist_rewrites(
    fake_llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents import knowledge

    calls: list[list[tuple[str, str | None]]] = []

    async def fake_each(queries: Any, config: Any) -> Any:
        calls.append(list(queries))
        return [([_hit(7, 0.9)], {"name": "search_knowledge_base", "status": "ok"})]

    monkeypatch.setattr(knowledge, "_search_each", fake_each)
    fake_llm.script(
        knowledge_rewrite=[rewrite(("antecipação Getnet", "pt"), ("advance Getnet", "en"))],
        knowledge=[answer("A antecipação adianta recebíveis [1].", 0.9, (1,))],
    )
    subtask = {"id": "s1", "agent": "knowledge", "query": "Como funciona a antecipação?"}
    await knowledge.knowledge_node({"subtask": subtask, "language": "pt"}, {})
    assert fake_llm.prompts("knowledge_rewrite") != ""
    # The question as written is searched on its own (started with the rewrite); afterwards
    # only the variants are searched, never the raw question twice.
    assert calls[0] == [("Como funciona a antecipação?", None)]
    assert calls[1] == [("antecipação Getnet", "pt"), ("advance Getnet", "en")]


def test_check_roles_use_the_checks_model_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.llm import model_for

    s = get_settings()
    monkeypatch.setattr(s, "openai_api_key", SecretStr("unused-test-key"))  # build only, no calls
    monkeypatch.setattr(s, "openai_model_primary", "primary-model")
    monkeypatch.setattr(s, "openai_model_fallback", "")
    monkeypatch.setattr(s, "openai_model_checks", "")
    assert model_for("input_guard") == "primary-model"  # default: nothing changes
    monkeypatch.setattr(s, "openai_model_checks", "gpt-5.4-nano")
    for role in ("input_guard", "output_guard", "knowledge_rewrite"):
        model = get_model(role).inner
        assert model.model_name == "gpt-5.4-nano", role
        assert model.temperature is None and model.reasoning_effort == "minimal", role
    assert get_model("knowledge").inner.model_name == "primary-model"
    assert get_model("knowledge").inner.temperature == 0.0


def test_checks_model_can_be_limited_to_some_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.llm import model_for

    s = get_settings()
    monkeypatch.setattr(s, "openai_model_primary", "primary-model")
    monkeypatch.setattr(s, "openai_model_checks", "checks-model")
    monkeypatch.setattr(s, "openai_model_checks_roles", " output_guard , knowledge ")
    assert model_for("output_guard") == "checks-model"
    assert model_for("input_guard") == "primary-model"
    assert model_for("knowledge_rewrite") == "primary-model"
    assert model_for("knowledge") == "primary-model"  # never a check role, even if listed
