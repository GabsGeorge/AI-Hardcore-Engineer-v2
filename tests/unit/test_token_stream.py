"""Answer text while it is generated: what may leave before the output guardrail runs."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.api.token_stream import HOLDBACK, AnswerFieldReader, TokenStream, safe_prefix

pytestmark = pytest.mark.req("REQ-API-07", "REQ-GR-04")

CPF = "529.982.247-25"  # valid check digits, so the mask recognises it
LONG = "A antecipação adianta os recebíveis das vendas no crédito para a conta do lojista. "


def _read(pieces: list[str]) -> str:
    reader = AnswerFieldReader()
    return "".join(reader.feed(p) for p in pieces)


def _split(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 11, 400])
def test_answer_field_is_decoded_whatever_the_chunking(size: int) -> None:
    answer = 'Linha 1\nLinha "2" com \\ barra, tab\t, acento ção, € e 😀 [1].'
    payload = json.dumps({"answer": answer, "confidence": 0.9, "cited": [1]})  # ASCII escapes
    assert _read(_split(payload, size)) == answer
    payload_utf8 = json.dumps({"answer": answer, "confidence": 0.9}, ensure_ascii=False)
    assert _read(_split(payload_utf8, size)) == answer


def test_only_the_answer_field_is_read_and_reading_stops_at_its_end() -> None:
    payload = '{"answer": "texto", "confidence": 0.9, "note": "\\"answer\\": \\"outro\\""}'
    assert _read(_split(payload, 4)) == "texto"


def test_an_object_that_does_not_start_with_answer_streams_nothing() -> None:
    payload = json.dumps({"variants": [{"query": '"answer": "x"', "lang": "pt"}], "answer": "não"})
    assert _read(_split(payload, 6)) == ""


def test_safe_prefix_holds_back_the_tail_and_never_cuts_a_word() -> None:
    text = LONG * 2
    prefix = safe_prefix(text)
    assert prefix is not None and text.startswith(prefix)
    assert len(text) - len(prefix) >= HOLDBACK
    assert prefix.endswith(" ")
    assert safe_prefix("curto") == ""


@pytest.mark.parametrize(
    "pii",
    [
        CPF,
        "11.222.333/0001-81",
        "4539 1488 0343 6467",
        "joao.silva@loja.example",
        "+55 51 99876-5432",
    ],
)
def test_pii_never_leaves_raw_at_any_point_of_the_stream(pii: str) -> None:
    text = f"{LONG}Seu cadastro usa {pii} como referência. {LONG}"
    digits = "".join(c for c in pii if c.isdigit())
    for end in range(1, len(text) + 1):
        prefix = safe_prefix(text[:end])
        assert prefix is not None
        assert pii not in prefix
        # Not even a recognisable piece of it: at most a few leading digits of the number.
        if digits:
            shown = "".join(c for c in prefix[len(LONG) :] if c.isdigit())
            assert len(shown) < 6, (end, prefix[-40:])
        else:
            assert "@loja.example" not in prefix


def test_a_financial_promise_stops_the_stream_before_it_shows() -> None:
    text = f"{LONG}Pode ficar tranquilo: vou estornar o valor hoje mesmo. {LONG}"
    shown = ""
    for end in range(1, len(text) + 1):
        prefix = safe_prefix(text[:end])
        if prefix is None:
            break
        shown = prefix
    else:
        pytest.fail("the promise never stopped the stream")
    assert "estornar" not in shown and shown.startswith(LONG)


def _chunk(text: str, run: str = "run-1", **kwargs: Any) -> AIMessageChunk:
    return AIMessageChunk(content=text, id=run, **kwargs)


def _feed(stream: TokenStream, chunks: list[AIMessageChunk], role: str) -> list[dict[str, Any]]:
    return [e for c in chunks for e in stream.feed(c, {"llm_role": role})]


def _plan(*agents: str) -> dict[str, Any]:
    return {"supervisor": {"plan": {"subtasks": [{"agent": a} for a in agents]}}}


def test_single_specialist_answer_is_streamed_and_other_roles_are_not() -> None:
    stream = TokenStream()
    stream.observe_update(_plan("support"))
    text = LONG * 3
    assert _feed(stream, [_chunk(p) for p in _split(text, 9)], "output_guard") == []
    assert _feed(stream, [_chunk(p) for p in _split(text, 9)], "supervisor") == []
    events = _feed(stream, [_chunk(p) for p in _split(text, 9)], "support")
    assert {e["event"] for e in events} == {"token"}
    sent = "".join(e["data"]["text"] for e in events)
    assert text.startswith(sent) and len(sent) > len(text) // 2
    # A complete message returned by a node (not a chunk of a model call) is never a token.
    assert list(stream.feed(AIMessage(content=text), {"llm_role": "support"})) == []


def test_with_several_specialists_only_the_synthesis_is_streamed() -> None:
    stream = TokenStream()
    stream.observe_update(_plan("knowledge", "support"))
    pieces = [_chunk(p) for p in _split(LONG * 3, 9)]
    assert _feed(stream, pieces, "support") == []
    args = _split(json.dumps({"answer": LONG * 3, "confidence": 0.9}, ensure_ascii=False), 9)
    knowledge = [
        AIMessageChunk(
            content="",
            id="run-k",
            tool_call_chunks=[{"name": None, "args": a, "id": None, "index": 0}],
        )
        for a in args
    ]
    assert _feed(stream, knowledge, "knowledge") == []
    events = _feed(stream, [_chunk(p, "run-s") for p in _split(LONG * 3, 9)], "synthesize")
    assert events and all(e["event"] == "token" for e in events)


def test_knowledge_structured_output_streams_the_answer_text_not_json() -> None:
    stream = TokenStream()
    stream.observe_update(_plan("knowledge"))
    answer = LONG * 3 + "Veja o app [1]."
    args = _split(json.dumps({"answer": answer, "confidence": 0.9, "cited": [1]}), 8)
    chunks = [
        AIMessageChunk(
            content="",
            id="run-k",
            tool_call_chunks=[{"name": None, "args": a, "id": None, "index": 0}],
        )
        for a in args
    ]
    sent = "".join(e["data"]["text"] for e in _feed(stream, chunks, "knowledge"))
    assert sent and answer.startswith(sent)
    assert "{" not in sent and "confidence" not in sent and "\\u" not in sent
    # The same JSON arriving as message content (provider json_schema mode) reads the same.
    other = TokenStream()
    other.observe_update(_plan("knowledge"))
    as_content = [_chunk(a, "run-j") for a in args]
    assert "".join(e["data"]["text"] for e in _feed(other, as_content, "knowledge")) == sent


def test_a_new_generation_resets_what_was_shown() -> None:
    stream = TokenStream()
    stream.observe_update(_plan("support"))
    first = _feed(stream, [_chunk(p, "run-1") for p in _split(LONG * 2, 9)], "support")
    assert first
    second = _feed(stream, [_chunk(p, "run-2") for p in _split("Outra " + LONG * 2, 9)], "support")
    assert second[0] == {"event": "token_reset", "data": {}}
    assert "".join(e["data"]["text"] for e in second[1:]).startswith("Outra ")


def test_after_a_promise_the_generation_emits_nothing_more() -> None:
    stream = TokenStream()
    stream.observe_update(_plan("support"))
    text = f"{LONG}Garanto o reembolso integral ainda hoje. {LONG * 3}"
    events = _feed(stream, [_chunk(p) for p in _split(text, 9)], "support")
    sent = "".join(e["data"]["text"] for e in events)
    assert "reembolso" not in sent and len(sent) <= len(LONG)
