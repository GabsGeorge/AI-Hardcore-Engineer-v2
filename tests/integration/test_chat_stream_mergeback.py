"""Streaming reconnect without duplicates (T056 / FR-013).

The scenario: the merchant's stream drops after the server has the message, the client reconnects
and sends the same turn again with the same ``Idempotency-Key``. The conversation must hold the
message once and the retry must replay the first answer instead of running the graph again.

``/v1/chat/stream`` does not implement that yet: it has no ``Idempotency-Key`` (only
``/v1/chat`` does) and the SPA does not retry a dropped stream; it shows "resposta interrompida"
and the merchant resends by hand (docs/limitations.md §6). The test is ``strict``: the day the
stream gains the replay, it starts passing and xfail(strict) turns that into a failure, so the
marker is removed together with the limitation entry.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.fakes import FakeLLM, make_plan
from tests.fixtures.kb import fresh_retriever, rewrite  # noqa: F401

pytestmark = pytest.mark.req("REQ-API-07")

MESSAGE = "O que é a Get Smart?"


def _done(body: str) -> dict[str, object]:
    """Payload of the stream's final ``done`` event."""
    event, payload = None, None
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:") and event == "done":
            payload = json.loads(line.removeprefix("data:").strip())
    assert isinstance(payload, dict), "the stream must end with a done event"
    return payload


@pytest.mark.xfail(
    strict=True,
    reason="reconnect not implemented: /v1/chat/stream has no Idempotency-Key replay and the SPA "
    "does not retry a dropped stream — see docs/limitations.md §6",
)
@pytest.mark.usefixtures("fresh_retriever")
async def test_retrying_a_dropped_stream_does_not_duplicate_the_turn(
    client: httpx.AsyncClient,
    fake_llm: FakeLLM,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    # Scripts for two runs, so a second (unwanted) run of the graph fails on the assertions
    # below rather than on an empty fake script.
    fake_llm.script(
        supervisor=[make_plan("knowledge"), make_plan("knowledge")],
        knowledge_rewrite=[rewrite(), rewrite()],
    )
    headers = {**auth_headers["cliente1988"], "Idempotency-Key": str(uuid.uuid4())}
    body = {"message": MESSAGE}

    first = await client.post("/v1/chat/stream", json=body, headers=headers)
    assert first.status_code == 200
    first_done = _done(first.text)

    # The client lost the stream and reconnects with the very same request.
    retry = await client.post("/v1/chat/stream", json=body, headers=headers)
    assert retry.status_code == 200
    retry_done = _done(retry.text)

    assert retry_done["conversation_id"] == first_done["conversation_id"], (
        "the retry opened a second conversation for the same turn"
    )
    assert retry_done["trace_id"] == first_done["trace_id"], "the retry must replay, not re-run"
    conversation = await client.get(
        f"/v1/conversations/{first_done['conversation_id']}", headers=auth_headers["cliente1988"]
    )
    assert conversation.status_code == 200
    asked = [m for m in conversation.json()["messages"] if m.get("content") == MESSAGE]
    assert len(asked) == 1, f"the message is in the conversation {len(asked)} times"
