"""Cada [n] da resposta é a n-ésima fonte listada (QA-017)."""

from __future__ import annotations

import pytest

from app.agents.knowledge import renumber_citations
from app.graph.state import Source


def _src(url: str) -> Source:
    return {"title": url.upper(), "url": url, "kind": "kb", "retrieved_at": "2026-11-26"}


@pytest.mark.req("REQ-RAG-06")
def test_markers_follow_the_deduplicated_source_list() -> None:
    # Passagens 1 e 2 são da mesma página "a"; o texto cita [3], [1][2] e [4].
    items = [_src("a"), _src("a"), _src("b"), _src("c")]
    text, sources = renumber_citations("x [3] y [1][2] w [4]", items, [0, 2, 3])
    assert [s["url"] for s in sources] == ["b", "a", "c"]
    assert text == "x [1] y [2] w [3]"


@pytest.mark.req("REQ-RAG-06")
def test_marker_outside_the_passages_is_dropped() -> None:
    text, sources = renumber_citations("fato [7].", [_src("a")], [0])
    assert text == "fato ."
    assert [s["url"] for s in sources] == ["a"]


@pytest.mark.req("REQ-RAG-06")
def test_cited_pages_without_markers_are_still_listed() -> None:
    text, sources = renumber_citations("sem marcadores", [_src("a"), _src("b")], [1])
    assert text == "sem marcadores"
    assert [s["url"] for s in sources] == ["b"]
