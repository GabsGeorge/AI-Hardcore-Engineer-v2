"""Fake-LLM scripts for every golden-set scenario (T126, SC-001, ADR 0006).

`evals/golden_set.yaml` holds the 10 scenarios of the challenge (sc-01..sc-10), the 8 extras
(sc-11..sc-18, English and variations) and the crediário ones (sc-19..sc-21). The real model is
measured against it by `evals/run_eval.py`; here each scenario gets a deterministic script per
graph role, so the same scenarios run in CI through the whole API (guardrails, supervisor,
specialists with real tools and retrieval, synthesis, verifier) without a model.

A script decides only what a model would decide (classification, plan, tool calls, answer text).
Routes, tool results, sources and handoffs come from the real graph, the seeded mock data
(`seed.sql`) and the knowledge passages seeded per scenario, and are scored with the same
`check()` as the real-model eval.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage

from tests.fakes import make_plan
from tests.fixtures.kb import answer, rewrite

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "evals" / "golden_set.yaml"
USAGE = {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100}
# Reference day of the mock data (conftest SEED_REFERENCE_DATE): "ontem" is 2026-10-13.
YESTERDAY = "2026-10-13"

SAFE: dict[str, Any] = {"classification": "safe"}
GROUNDED: dict[str, Any] = {"grounded": True, "reply_supported": True, "about_other_thing": False}


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    """Support turn that asks for tools (run for real against the seeded mock data)."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{i}"} for i, (name, args) in enumerate(calls)
        ],
        usage_metadata=USAGE,
    )


def reply(text: str) -> AIMessage:
    return AIMessage(content=text, usage_metadata=USAGE)


@dataclass(frozen=True)
class Turn:
    """Scripts for one turn, by role, in the order the graph asks for them."""

    scripts: dict[str, list[Any]]
    # Knowledge passages seeded before the turn: (title, section, content).
    kb: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    message: str
    language: str
    expected: dict[str, Any]
    turn: Turn
    context_message: str | None = None
    context_turn: Turn | None = None


def _knowledge(message: str, language: str, text: str, kb_content: str) -> Turn:
    return Turn(
        scripts={
            "input_guard": [SAFE],
            "supervisor": [make_plan("knowledge", language=language, query=message)],
            "knowledge_rewrite": [rewrite()],
            "knowledge": [answer(text, 0.9, (1,))],
            "output_guard": [GROUNDED],
        },
        kb=(("Central de Ajuda Getnet", message, f"{message} {kb_content}"),),
    )


def _support(message: str, language: str, calls: AIMessage, text: str) -> Turn:
    return Turn(
        scripts={
            "input_guard": [SAFE],
            "supervisor": [make_plan("support", language=language, query=message)],
            "support": [calls, reply(text)],
        }
    )


def _fanout(message: str, language: str, kb_content: str, calls: AIMessage, synthesis: str) -> Turn:
    return Turn(
        scripts={
            "input_guard": [SAFE],
            "supervisor": [make_plan("knowledge", "support", language=language, query=message)],
            "knowledge_rewrite": [rewrite()],
            "knowledge": [answer("Parte de produto respondida pela base [1].", 0.9, (1,))],
            "support": [calls, reply("Parte da conta respondida pelas ferramentas.")],
            "synthesize": [reply(synthesis)],
            "output_guard": [GROUNDED],
        },
        kb=(("Central de Ajuda Getnet", message, f"{message} {kb_content}"),),
    )


def _escalation(message: str, language: str, classification: str, sensitive: bool) -> Turn:
    return Turn(
        scripts={
            "input_guard": [{"classification": classification}],
            "supervisor": [
                make_plan("escalation", language=language, query=message, sensitive=sensitive)
            ],
            "escalation": [reply("Lojista pede atendimento humano.")],
        }
    )


def _blocked(guard: dict[str, Any]) -> Turn:
    # The heuristics may block before the classifier runs; the scripts left over are unused.
    return Turn(scripts={"input_guard": [guard], "supervisor": [make_plan("knowledge")]})


def _turn_for(sc: dict[str, Any]) -> tuple[Turn, Turn | None]:
    sid, msg, lang = sc["id"], sc["message"], sc["language"]
    settle = tool_calls(("get_settlement_schedule", {"from_date": YESTERDAY, "to_date": YESTERDAY}))
    offline = tool_calls(("diagnose_terminal", {}))
    scripts: dict[str, tuple[Turn, Turn | None]] = {
        "sc-01-knowledge-antecipacao": (
            _knowledge(msg, lang, "A antecipação adianta recebíveis [1].", "adianta recebíveis"),
            None,
        ),
        "sc-02-knowledge-pix": (
            _knowledge(
                msg, lang, "O Pix entra na taxa por transação (MDR) [1].", "taxa por transação MDR"
            ),
            None,
        ),
        "sc-03-support-settlement": (
            _support(msg, lang, settle, "Suas vendas de ontem caem conforme a agenda."),
            None,
        ),
        "sc-04-support-terminal": (
            _support(
                msg,
                lang,
                tool_calls(("diagnose_terminal", {}), ("lookup_decline_code", {"code": "E12"})),
                "Não encontrei o erro E12 nos dados das suas maquininhas.",
            ),
            None,
        ),
        "sc-05-fanout": (
            _fanout(msg, lang, "antecipação adianta recebíveis", offline, "Resposta composta [1]."),
            None,
        ),
        "sc-06-knowledge-link": (
            _knowledge(msg, lang, "Gere o link no app Getnet [1].", "link de pagamento WhatsApp"),
            None,
        ),
        "sc-07-explicit-human": (_escalation(msg, lang, "human_request", False), None),
        "sc-08-sensitive-dispute": (_escalation(msg, lang, "sensitive", True), None),
        "sc-09-injection": (_blocked({"classification": "injection"}), None),
        "sc-10-out-of-scope": (
            _blocked({"classification": "out_of_scope", "topic": "other"}),
            None,
        ),
        "sc-11-en-knowledge": (
            _knowledge(msg, lang, "Receivables advance pays sales early [1].", "pays early"),
            None,
        ),
        "sc-12-en-support": (
            _support(msg, lang, settle, "Yesterday's sales settle per your schedule."),
            None,
        ),
        "sc-13-en-fanout": (
            _fanout(msg, lang, "Pix fees per transaction", offline, "Composed answer [1]."),
            None,
        ),
        "sc-14-en-human": (_escalation(msg, lang, "human_request", False), None),
        "sc-15-idor-attempt": (
            _blocked(
                {"classification": "injection", "data_owner": "other_party", "reason": "terceiro"}
            ),
            None,
        ),
        "sc-16-pii-echo": (
            _knowledge(msg, lang, "O próximo passo é confirmar o cadastro [1].", "próximo passo"),
            None,
        ),
        "sc-17-followup": (
            _knowledge(msg, lang, "A Get Smart tem o preço do plano [1].", "Get Smart preço"),
            _knowledge(
                sc.get("context_message", ""), lang, "A Get Smart é Android [1].", "Get Smart"
            ),
        ),
        "sc-18-support-decline": (
            _support(
                msg,
                lang,
                tool_calls(("lookup_decline_code", {"code": "05"})),
                "O código 05 significa transação não autorizada.",
            ),
            None,
        ),
        "sc-19-knowledge-crediario": (
            _knowledge(msg, lang, "O crediário parcela em até 12x [1].", "crediário parcelas"),
            None,
        ),
        "sc-20-fanout-crediario": (
            _fanout(msg, lang, "taxas do crediário", settle, "Resposta composta [1]."),
            None,
        ),
        "sc-21-en-crediario": (
            _knowledge(msg, lang, "Crediário splits sales in installments [1].", "installments"),
            None,
        ),
    }
    if sid not in scripts:
        raise KeyError(
            f"golden scenario {sid} has no fake-LLM script in tests/integration/scenarios.py"
        )
    return scripts[sid]


def load() -> list[Scenario]:
    """Every golden-set scenario with its script; fails if one is missing a script."""
    data = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    out = []
    for sc in data["scenarios"]:
        turn, context_turn = _turn_for(sc)
        out.append(
            Scenario(
                id=sc["id"],
                message=sc["message"],
                language=sc["language"],
                expected=dict(sc["expected"]),
                turn=turn,
                context_message=sc.get("context_message"),
                context_turn=context_turn,
            )
        )
    return out
