"""Support turns where the merchant's account has no matching data (REQ-GR-04).

With a bare `[]` from `diagnose_terminal`, the specialist said "could not retrieve" and added
generic troubleshooting (cables, restart) that no data supports, and the output verifier replaced
the whole compound reply. Now the evidence spells out "no records" and the specialist reports
exactly that. The verifier needs no extra rule for it: with this evidence and the grounding prompt
as it is, it accepted "não encontrei maquininha cadastrada" 8/8 and blocked invented diagnoses 6/6,
while an extra "no records" rule made it reject honest fee answers (5/8 instead of 8/8;
docs/reports/data/checks-model-2026-11-27.json).
"""

import pytest

from app.agents.support import account_evidence
from app.core.prompts import load_prompt

pytestmark = pytest.mark.req("REQ-GR-04")


@pytest.mark.parametrize("empty", [[], {}, None])
def test_empty_tool_result_is_spelled_out_for_the_verifier(empty: object) -> None:
    text = account_evidence("diagnose_terminal", empty)
    assert text.startswith("(account data from tool diagnose_terminal)\n")
    assert "No records" in text and "no card machine registered" in text


def test_non_empty_result_is_the_raw_json() -> None:
    text = account_evidence("get_settlement_schedule", [{"amount": 46065}])
    assert text.endswith('[{"amount": 46065}]')


def test_support_prompt_forbids_generic_troubleshooting() -> None:
    prompt = load_prompt("support")
    assert "Troubleshooting steps come ONLY from `suggested_checks`" in prompt
    assert "não encontrei maquininha cadastrada na sua conta" in prompt


def test_support_prompt_says_when_the_named_error_is_not_in_the_data() -> None:
    """Golden sc-04 asks about "erro E12"; the data shows NO_SIGNAL. The reply reported NO_SIGNAL
    and never said that E12 was not found, so the merchant's question went unanswered."""
    prompt = load_prompt("support")
    assert "não encontrei o erro E12 nos dados das suas" in prompt
    assert "Never explain what that code means" in prompt
