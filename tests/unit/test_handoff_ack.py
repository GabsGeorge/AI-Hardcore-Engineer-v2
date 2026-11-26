"""QA-R3-004: a turn that ends in a pending handoff must not return an empty `response`.

The graph pauses inside the escalation node, so no reply is synthesized. The API now answers
with a short localized notice; a turn that did produce a reply keeps it untouched."""

from uuid import uuid4

import pytest

from app.api.chat import HANDOFF_ACK, build_response


def _state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "results": [],
        "guardrail_flags": [],
        "route_trace": ["input_guardrail", "supervisor", "escalation"],
        "final_response": None,
        "language": "pt",
        "escalation": {"status": "awaiting_human", "ticket_id": str(uuid4())},
    }
    state.update(overrides)
    return state


@pytest.mark.parametrize("language", ["pt", "en"])
def test_pending_handoff_gets_a_localized_notice(language: str) -> None:
    response = build_response(_state(language=language), uuid4(), "a" * 32)
    assert response.response == HANDOFF_ACK[language]
    assert response.handoff.status == "awaiting_human"
    assert response.sources == []


def test_unknown_language_falls_back_to_portuguese() -> None:
    response = build_response(_state(language=None), uuid4(), "a" * 32)
    assert response.response == HANDOFF_ACK["pt"]


def test_a_synthesized_reply_is_kept() -> None:
    response = build_response(_state(final_response="Resposta real."), uuid4(), "a" * 32)
    assert response.response == "Resposta real."


def test_no_handoff_and_no_reply_stays_empty() -> None:
    state = _state(escalation=None)
    assert build_response(state, uuid4(), "a" * 32).response == ""
