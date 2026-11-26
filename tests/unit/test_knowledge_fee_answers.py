"""Fee questions whose answer is "it depends on your contract" (golden sc-02, sc-13, sc-20).

The help center says the per-transaction fee (MDR) also applies to Pix and that the crediário
discount rate is in the contract and in the app. The knowledge agent used to open with "não
encontrei", rate itself 0.2 and send the turn to a human. The prompt now treats that statement as
the answer, and the query rewrite searches the MDR page for any fee question.
"""

import pytest

from app.core.prompts import load_prompt

pytestmark = pytest.mark.req("REQ-RAG-05", "REQ-RAG-07")


def test_fee_that_is_not_a_public_number_is_answered_not_reported_missing() -> None:
    answer_prompt, rewrite_prompt = load_prompt("knowledge").split("<!-- query-rewrite -->")
    assert "Fees, prices and deadlines that are not one public number" in answer_prompt
    assert 'Do not open with\n    "não encontrei"' in answer_prompt
    # "not found" keeps its low confidence when the context says nothing about the fee.
    assert 'Use "not found" (at most 0.2) only when the context says nothing' in answer_prompt
    assert "`taxa por transação MDR Getnet`" in rewrite_prompt
