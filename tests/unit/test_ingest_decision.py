"""O job de ingestão só lê o site quando precisa (a base sobrevive a reinícios no volume)."""

from __future__ import annotations

import pytest

from app.rag.ingest import crawl_decision, reranker_enabled


@pytest.mark.req("REQ-RAG-01")
def test_populated_base_is_not_recrawled_on_restart() -> None:
    assert (
        crawl_decision(has_key=True, existing_documents=96, refresh=False)
        == "knowledge_base_present"
    )


@pytest.mark.req("REQ-RAG-01")
def test_empty_base_is_crawled() -> None:
    assert crawl_decision(has_key=True, existing_documents=0, refresh=False) is None


@pytest.mark.req("REQ-RAG-01")
def test_explicit_refresh_recrawls_a_populated_base() -> None:
    assert crawl_decision(has_key=True, existing_documents=96, refresh=True) is None


@pytest.mark.req("REQ-RAG-01")
def test_without_a_key_nothing_is_crawled() -> None:
    assert crawl_decision(has_key=False, existing_documents=0, refresh=True) == (
        "OPENAI_API_KEY is empty"
    )


def test_reranker_download_follows_skip_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKIP_RERANKER", "1")
    assert reranker_enabled() is False
    monkeypatch.setenv("SKIP_RERANKER", "0")
    assert reranker_enabled() is True
