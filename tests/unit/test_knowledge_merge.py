"""Multi-query merge in the knowledge agent (REQ-RAG-05): every query variant contributes its
best passage before the rest is filled by score (golden sc-02: the Pix pages pushed out the MDR
page that the `taxa por transação` variant had found)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app.agents.knowledge import merge_hits
from app.core.config import get_settings
from app.core.llm import get_model
from app.rag.retriever import KbHit

pytestmark = pytest.mark.req("REQ-RAG-05")


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


def test_each_variant_keeps_its_best_passage() -> None:
    pix = [_hit(i, 0.95 - i / 100) for i in range(1, 7)]  # six strong Pix chunks
    mdr = [_hit(100, 0.80)]  # the only hit of the fee variant
    merged = merge_hits([pix, mdr], k=5)
    assert 100 in {h.chunk_id for h in merged}
    assert len(merged) == 5
    assert [h.score for h in merged] == sorted((h.score for h in merged), reverse=True)


def test_duplicates_keep_the_best_score_and_count_once() -> None:
    merged = merge_hits([[_hit(1, 0.5), _hit(2, 0.4)], [_hit(1, 0.9)]], k=5)
    assert [(h.chunk_id, h.score) for h in merged] == [(1, 0.9), (2, 0.4)]


def test_empty_variants_are_ignored() -> None:
    assert merge_hits([[], [_hit(3, 0.7)], []]) == [_hit(3, 0.7)]


def test_planner_knowledge_and_verifier_run_at_temperature_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "openai_api_key", SecretStr("unused-test-key"))  # build only, no calls
    monkeypatch.setattr(s, "openai_model_primary", "primary-model")
    monkeypatch.setattr(s, "openai_model_fallback", "")
    for role in ("supervisor", "knowledge_rewrite", "knowledge", "output_guard"):
        assert get_model(role).inner.temperature == 0.0, role
