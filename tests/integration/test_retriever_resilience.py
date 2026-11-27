"""Block review findings 2 and 7: reranker download and BM25 rebuild under timeouts."""

import asyncio
import threading
import time
from typing import Any

import pytest

from app.db.pool import Pool
from app.rag import retriever
from app.rag.chunking import passage
from tests.fixtures.kb import MAQUININHAS, add_document, seed_kb

pytestmark = pytest.mark.req("REQ-RAG-04", "REQ-REL-01")


async def test_bm25_rebuild_survives_a_cancelled_caller(
    db_pool: Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_document(db_pool, "https://www.getnet.com.br/x", "X", "pt", [("S", "get smart")])
    monkeypatch.setattr(retriever, "_cache", retriever.IndexCache())
    real = retriever.Bm25Index

    def slow_index(rows: Any) -> Any:
        time.sleep(0.3)
        return real(rows)

    monkeypatch.setattr(retriever, "Bm25Index", slow_index)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(retriever.bm25_index(db_pool), 0.05)
    await asyncio.sleep(0.6)
    assert retriever._cache.index is not None  # the build finished and was kept
    assert retriever._cache.fingerprint is not None


def test_concurrent_rerank_during_download_fails_fast() -> None:
    ranker = retriever.FlashRankReranker()
    ranker._lock.acquire()  # another thread is downloading the model
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="warming up"):
            ranker.rerank("q", ["p"])
        assert time.monotonic() - started < 0.5  # no thread parked on the lock
    finally:
        ranker._lock.release()


def test_failed_warmup_removes_a_partial_model_dir(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = tmp_path / "flashrank" / retriever.RERANK_MODEL
    model_dir.mkdir(parents=True)
    (model_dir / "half-extracted.onnx").write_bytes(b"x")
    monkeypatch.setattr(retriever, "_flashrank_dir", lambda: tmp_path / "flashrank")

    def broken() -> Any:
        raise OSError("corrupted model")

    monkeypatch.setattr(retriever, "_load_ranker", broken)
    with pytest.raises(OSError):
        retriever.warm_reranker()
    assert not model_dir.exists()


def test_warmup_loads_the_model_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    calls: list[Any] = []

    class FakeRanker:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

        def rerank(self, request: Any) -> list[dict[str, Any]]:
            return [{"id": 0, "score": 0.9}]

    monkeypatch.setattr(retriever, "_load_ranker", lambda: FakeRanker(model_name="fake"))
    monkeypatch.setattr(retriever, "_flashrank_dir", lambda: tmp_path / "flashrank")
    monkeypatch.setattr(retriever, "_reranker", None)
    retriever.warm_reranker()
    assert retriever.get_reranker().rerank("q", ["p"]) == [(0, 0.9)]
    assert len(calls) == 1
    assert threading.active_count() >= 1


class _BrokenReranker:
    def rerank(self, query: str, passages: list[str]) -> list[tuple[int, float]]:
        raise RuntimeError("cross-encoder down")


async def test_rerank_failure_falls_back_to_the_calibrated_dense_search(
    db_pool: Pool, fake_llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA review N7: the fallback used to give every RRF candidate a fixed 0.6, above
    RAG_RELEVANCE_THRESHOLD, so passages no model had validated passed the filter. Now it is
    the dense strategy, with its per-passage threshold."""
    await seed_kb(db_pool)
    monkeypatch.setattr(retriever, "_cache", retriever.IndexCache())
    retriever.set_reranker(_BrokenReranker())
    try:
        # The fake embeddings are deterministic: the text of a stored passage is its own best
        # match, so the dense strategy has a hit above its threshold.
        query = passage("Maquininhas Getnet", *MAQUININHAS[0])
        fallback = await retriever._search(query, "pt", "hybrid_rerank")
        dense = await retriever._search(query, "pt", "dense")
    finally:
        retriever.set_reranker(None)
    assert dense, "the seeded KB must answer this query, or the comparison proves nothing"
    assert [(h.chunk_id, h.score) for h in fallback] == [(h.chunk_id, h.score) for h in dense]
    assert all(h.score != 0.6 for h in fallback)


async def test_rerank_failure_with_nothing_above_the_dense_threshold_returns_nothing(
    db_pool: Pool, fake_llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_kb(db_pool)
    monkeypatch.setattr(retriever, "_cache", retriever.IndexCache())
    monkeypatch.setenv("RAG_DENSE_THRESHOLD", "0.999")
    from app.core.config import get_settings

    get_settings.cache_clear()
    retriever.set_reranker(_BrokenReranker())
    try:
        assert await retriever._search("maquininha Get Smart", "pt", "hybrid_rerank") == []
    finally:
        retriever.set_reranker(None)
        monkeypatch.delenv("RAG_DENSE_THRESHOLD")
        get_settings.cache_clear()
