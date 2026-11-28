"""`token` events of `/v1/chat/stream` (REQ-API-07, contracts/events.md).

The model here is `StreamingFakeChat`, a real LangChain chat model that streams its scripted
reply, so the turn goes through LangGraph's "messages" mode as it does with the provider: every
role streams, and the endpoint must forward only the answer text, masked and held back.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core import llm
from app.core.config import get_settings
from app.db.migrate import apply_seed
from app.db.pool import Pool
from tests.fakes import StreamingFakeLLM, fake_embeddings, make_plan
from tests.fixtures.kb import add_document, answer, fresh_retriever, rewrite  # noqa: F401
from tests.integration.scenarios import GROUNDED, SAFE, reply, tool_calls

pytestmark = [pytest.mark.req("REQ-API-07"), pytest.mark.usefixtures("fresh_retriever")]

QUESTION = "Como funciona a antecipação de recebíveis?"
PASSAGE = "A antecipação adianta os recebíveis das vendas no crédito para a conta do lojista."
LONG = "A antecipação adianta os recebíveis das vendas no crédito para a conta do lojista. "
CPF = "529.982.247-25"


@pytest.fixture
def stream_llm() -> Iterator[StreamingFakeLLM]:
    fake = StreamingFakeLLM()
    llm.set_overrides(model_factory=fake.model, embeddings=fake_embeddings())
    yield fake
    llm.set_overrides(model_factory=None, embeddings=None)


@pytest.fixture
async def stream_client(app: FastAPI, stream_llm: StreamingFakeLLM, db_pool: Pool) -> Any:
    await app.state.redis.flushdb()
    await add_document(
        db_pool,
        "https://site.getnet.com.br/antecipacao/",
        "Antecipação",
        "pt",
        [(QUESTION, PASSAGE)],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _events(body: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    event: str | None = None
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:") and event is not None:
            out.append((event, json.loads(line.removeprefix("data:").strip())))
            event = None
    return out


def _tokens(events: list[tuple[str, Any]]) -> str:
    """The draft as the client holds it: tokens appended, cleared on token_reset."""
    draft = ""
    for name, data in events:
        if name == "token":
            draft += data["text"]
        elif name == "token_reset":
            draft = ""
    return draft


def _knowledge_scripts(text: str, grounded: dict[str, Any] = GROUNDED) -> dict[str, list[Any]]:
    return {
        "input_guard": [SAFE],
        "supervisor": [make_plan("knowledge", query=QUESTION)],
        "knowledge_rewrite": [rewrite()],
        "knowledge": [answer(text, 0.9, (1,))],
        "output_guard": [grounded],
    }


async def _ask(client: httpx.AsyncClient, headers: dict[str, str]) -> list[tuple[str, Any]]:
    resp = await client.post("/v1/chat/stream", json={"message": QUESTION}, headers=headers)
    assert resp.status_code == 200, resp.text
    return _events(resp.text)


async def test_knowledge_answer_streams_as_text_before_done(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    text = LONG * 3 + "Veja o valor no app Getnet [1]."
    stream_llm.script(**_knowledge_scripts(text))
    events = await _ask(stream_client, auth_headers["cliente1988"])
    names = [n for n, _ in events]
    assert names[-1] == "done" and "token" in names and "agent_step" in names
    assert names.index("token") < names.index("done")
    draft = _tokens(events)
    final = events[-1][1]["response"]
    assert final == text
    assert draft and final.startswith(draft), draft[-60:]
    # Only the answer: nothing of the JSON envelope, the plan, the classifier or the verifier.
    for leaked in ("confidence", "cited", "subtasks", "classification", "reply_supported", "{"):
        assert leaked not in draft


async def test_pii_in_the_answer_never_reaches_the_stream_raw(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    text = f"{LONG}O cadastro está no documento {CPF} e no e-mail dono@loja.example. {LONG * 2}"
    stream_llm.script(**_knowledge_scripts(text))
    events = await _ask(stream_client, auth_headers["cliente1988"])
    raw = "".join(json.dumps(d, ensure_ascii=False) for n, d in events if n == "token")
    assert CPF not in raw and "529.982" not in raw and "dono@loja.example" not in raw
    assert "[CPF]" in _tokens(events)
    assert CPF not in events[-1][1]["response"]


async def test_financial_promise_stops_the_stream_and_done_brings_the_rewrite(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    text = f"{LONG}Pode ficar tranquilo: vou estornar o valor hoje mesmo [1]. {LONG * 3}"
    stream_llm.script(**_knowledge_scripts(text))
    events = await _ask(stream_client, auth_headers["cliente1988"])
    draft = _tokens(events)
    assert "estornar" not in draft and len(draft) <= len(LONG)
    done = events[-1][1]
    assert done["guardrails"]["output"] == "rewritten"
    assert "vou estornar" not in done["response"]


async def test_answer_blocked_by_the_verifier_is_replaced_by_done(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    text = LONG * 3 + "A taxa é de 0,10% [1]."
    blocked = {"grounded": False, "reply_supported": False, "about_other_thing": False}
    stream_llm.script(**_knowledge_scripts(text, blocked))
    events = await _ask(stream_client, auth_headers["cliente1988"])
    draft = _tokens(events)
    done = events[-1][1]
    assert draft, "the draft was shown before the verifier ran"
    assert done["guardrails"]["output"] == "blocked"
    # The contract: the client shows `done.response`, never the draft, once `done` arrives.
    assert not done["response"].startswith(draft)


async def test_fanout_streams_the_synthesis_only(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    await apply_seed(db_pool, date(2026, 10, 13))
    specialist = "TEXTO-DO-ESPECIALISTA " * 12
    synthesis = "SINTESE: " + LONG * 3 + "[1]."
    stream_llm.script(
        input_guard=[SAFE],
        supervisor=[make_plan("knowledge", "support", query=QUESTION)],
        knowledge_rewrite=[rewrite()],
        knowledge=[answer(specialist + "[1].", 0.9, (1,))],
        support=[tool_calls(("diagnose_terminal", {})), reply(specialist)],
        synthesize=[reply(synthesis)],
        output_guard=[GROUNDED],
    )
    events = await _ask(stream_client, auth_headers["cliente1988"])
    draft = _tokens(events)
    assert draft.startswith("SINTESE: ") and "TEXTO-DO-ESPECIALISTA" not in draft
    assert events[-1][1]["response"].startswith(draft)


async def test_support_answer_streams_after_its_tool_calls(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    await apply_seed(db_pool, date(2026, 10, 13))
    text = "Sua Get Mini está offline por falta de sinal. " * 4
    stream_llm.script(
        input_guard=[SAFE],
        supervisor=[make_plan("support", query="Minha maquininha não conecta")],
        support=[tool_calls(("diagnose_terminal", {})), reply(text)],
    )
    events = await _ask(stream_client, auth_headers["cliente1988"])
    draft = _tokens(events)
    assert draft and events[-1][1]["response"].startswith(draft)
    assert "diagnose_terminal" not in draft


async def test_without_the_flag_the_stream_has_no_token_events(
    stream_client: httpx.AsyncClient,
    stream_llm: StreamingFakeLLM,
    auth_headers: dict[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "chat_stream_tokens", False)
    text = LONG * 3 + "[1]."
    stream_llm.script(**_knowledge_scripts(text))
    events = await _ask(stream_client, auth_headers["cliente1988"])
    names = {n for n, _ in events}
    assert "token" not in names and "done" in names
    assert events[-1][1]["response"] == text
