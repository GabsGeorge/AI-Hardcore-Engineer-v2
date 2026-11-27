"""The verifier's [n] is the page the answer cites as [n] (golden sc-02).

The knowledge answer renumbers its markers per cited page ("taxa por transação [1]" = MDR page),
while the verifier numbered the passages in search order ([1] = the Pix page). The verifier then
checked the fee claim against the wrong passage and replaced correct Pix fee answers."""

import pytest

from app.agents.knowledge import evidence_by_source, renumber_citations
from app.graph.state import Source
from app.guardrails.output import _sources_block

pytestmark = pytest.mark.req("REQ-GR-04")


def _src(url: str) -> Source:
    return {"title": url, "url": url, "kind": "kb", "retrieved_at": "2026-11-27T00:00:00+00:00"}


ITEMS = [
    ("Pix — Getnet (pix)", "Receba por Pix na maquininha."),
    ("Pix por aproximação (aprox)", "Pagamentos quase instantâneos."),
    ("Taxas MDR (mdr)", "Taxa de transação: crédito, débito, Pix ou parcelamento."),
    ("Taxas MDR (mdr)", "Taxa de adesão: única."),
]
ITEM_SOURCES = [_src("pix"), _src("aprox"), _src("mdr"), _src("mdr")]


def test_evidence_follows_the_renumbered_citations() -> None:
    answer, sources = renumber_citations(
        "O Pix paga a taxa por transação [3] e há taxa de adesão [4].", ITEM_SOURCES, [2, 3]
    )
    assert answer == "O Pix paga a taxa por transação [1] e há taxa de adesão [1]."
    evidence = evidence_by_source(ITEMS, ITEM_SOURCES, sources)
    assert "Taxa de transação: crédito, débito, Pix" in evidence[0]
    assert "Taxa de adesão" in evidence[0]  # both MDR passages are entry [1]
    assert [e.split("\n")[0] for e in evidence[1:]] == [ITEMS[0][0], ITEMS[1][0]]


def test_verifier_numbers_each_answer_from_one() -> None:
    kb = {
        "agent": "knowledge",
        "evidence": evidence_by_source(ITEMS, ITEM_SOURCES, [_src("mdr")]),
        "sources": [_src("mdr")],
    }
    support = {
        "agent": "support",
        "evidence": ['(account data from tool diagnose_terminal)\n[{"status": "offline"}]'],
        "sources": [],
    }
    block = _sources_block([support, kb])
    support_part, kb_part = block.split("Sources of the knowledge answer:")
    assert support_part.startswith("Sources of the support answer:\n[1] (account data")
    assert kb_part.lstrip().startswith("[1] Taxas MDR (mdr)")


def test_sources_without_evidence_are_numbered_per_answer() -> None:
    block = _sources_block([{"agent": "knowledge", "evidence": [], "sources": [_src("a")]}])
    assert block == "Sources of the knowledge answer:\n[1] (kb) a — a"
