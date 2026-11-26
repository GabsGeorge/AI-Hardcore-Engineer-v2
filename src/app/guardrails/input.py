"""Input guardrail node (REQ-GR-01, REQ-GR-02, REQ-GR-03).

Three barriers run in order: (1) PII masking (second barrier after `turn_input` in the API),
(2) regex heuristics that short-circuit the loudest attacks without an LLM call, and
(3) an LLM classifier (`input_guard`) with structured output. A blocking verdict sets
``guardrail_flags = ["input:blocked"]`` so ``route_after_input`` sends the turn to
``safe_response``. ``sensitive`` and ``human_request`` do not block (constitution IV): they are
appended as extra flags that the Supervisor can inspect without the API seeing them, since the
contract literals for the ``input`` field are only ``pass | masked | blocked``.
"""

import asyncio
import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.llm import get_model
from app.core.prompts import load_prompt
from app.graph.memory import MEMORY_MAX_MESSAGES
from app.graph.state import AgentState
from app.guardrails.pii import mask
from app.observability.metrics import GUARDRAIL_BLOCKS

Classification = Literal["safe", "injection", "out_of_scope", "sensitive", "human_request"]


class InputGuardResult(BaseModel):
    """Structured output of the ``input_guard`` role (REQ-GR-01, REQ-GR-03).

    Field order is the order the model writes them: the short ``reason`` first, then the two
    questions that decide an attack (what an instruction targets, who the text says owns the
    data), the topic, and only then the ``classification``. With the label first, gpt-4o-mini
    wrote "ignore aimed at the previous message" as the reason and still answered `injection`.
    The defaults keep older scripted fakes (classification only) valid.
    """

    reason: str = Field(default="", max_length=200)
    instruction_target: Literal["none", "conversation", "assistant_rules"] = "none"
    data_owner: Literal["no_account_data", "merchant", "other_party"] = "no_account_data"
    topic: Literal["getnet", "weather_or_exchange", "conversation", "other"] = "getnet"
    classification: Classification


def verdict(result: InputGuardResult) -> Classification:
    """Blocking classes win if the label OR the fields behind it say so (never the reverse):
    an `assistant_rules` target or an `other_party` owner is an attack even when the label
    drifted, and an `other` topic is out of scope."""
    if (
        result.classification == "injection"
        or result.instruction_target == "assistant_rules"
        or result.data_owner == "other_party"
    ):
        return "injection"
    if result.classification == "out_of_scope" or result.topic == "other":
        return "out_of_scope"
    return result.classification


# Heuristics (REQ-GR-01): short-circuit the loudest attacks without any LLM call. These cover
# ignore-the-rules, roleplay, prompt-extraction and the encoded-instructions families.
#
# Every "ignore / forget / disregard" and "act as / pretend" pattern needs a TARGET (the rules,
# the instructions, the prompt, the token, an unrestricted persona...). Merchants write
# "Ignore the previous message, I meant Pix" or "Can you act as my guide?" all the time; the
# verb alone is not an attack (tests/unit/test_input_guardrail_false_positives.py).
#
# There is no heuristic for CPF/CNPJ/e-mail in the text: identity comes only from the token
# and the tools never accept another customer (ADR 0005), so a document number in a sentence
# is not an IDOR and blocking it only hurt legitimate merchants ("Mostre as transações do CNPJ
# da minha empresa"). The LLM classifier still sees every message.
_INSTRUCTION_TARGET = (
    r"(regras|regra|instru[cç][õo]es|instru[cç][ãa]o|diretrizes|orienta[cç][õo]es|"
    r"pol[ií]ticas|system\s+prompt|prompt|rules|instructions|guidelines|directives|policies|"
    r"token|identidade|identity|authenticated\s+user|usu[áa]rio\s+autenticado|"
    r"(?:everything|tudo)\s+(?:above|acima))"
)
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(ignor[ea]|ignore|desconsidere|disregard|esque[cç]a|forget)\b"
        r"(?:\s+[\w'’]+){0,4}?\s+" + _INSTRUCTION_TARGET + r"\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(finja|pretenda|pretend|act\s+as|aja\s+como|roleplay|"
        r"a\s+partir\s+de\s+agora\s+(?:aja|voc[eê]|seja))\b.{0,60}?"
        r"\b(sem\s+(?:regras|restri[cç][õo]es|filtros|limites)|"
        r"without\s+(?:rules|restrictions|filters|limits)|no\s+(?:rules|restrictions|filters)|"
        r"dan|jailbr\w*|dev\s+mode|developer\s+mode|modo\s+desenvolvedor|unrestricted|"
        r"irrestrit\w*|ceo|presidente|diretor\w*|administrador|administrator|admin|"
        r"system|sistema)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    # Identity override in plain words: "eu sou o cliente2024", "Pretend I am cliente2024".
    re.compile(
        r"\b(eu\s+sou|sou|i\s+am|i'm)\s+(?:o\s+|a\s+)?"
        r"(cliente|operador|operator|admin|administrador|lojista|merchant)\s*\d+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(obey\s+only\s+me|obede[cç]a\s+(?:s[oó]\s+)?a\s+mim)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(you\s+are\s+now|voc[eê]\s+(agora\s+)?[eé])\s+(dan|jailbroken|dev\s+mode|"
        r"a\s+jailbroken|a\s+system\s+administrator|the\s+system\s+administrator)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(mostre|revele|reveal|print|exiba|repita|repeat|show|display|tell\s+me|diga)\b"
        r"\W+.{0,40}?"
        r"(system\s+prompt|system\s+message|prompt\s+do\s+sistema|"
        r"instru[cç][õo]es\s+(do\s+sistema|internas|iniciais|ocultas)|"
        r"internal\s+(rules|instructions)|regras\s+(do\s+sistema|internas|iniciais|ocultas)|"
        r"(initial|hidden|original)\s+(rules|instructions|prompt)|"
        r"your\s+(system\s+)?(prompt|instructions|rules)|suas\s+(instru[cç][õo]es|regras))",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"nova\s+(regra|instru[cç][ãa]o)\s*(do\s+sistema)?\s*[:,]",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(decode|decodifique|converta|interpret|translate)\b.{0,40}?"
        r"(base64|rot13|hex|binary|binário|codificad|bin[áa]rio)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(base64|rot13)\s*[:,]|\b(em\s+rot13|em\s+base64|in\s+base64|in\s+rot13)\b",
        re.IGNORECASE,
    ),
]


def _heuristic_hit(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


_CONTEXT_CHARS = 400


def _previous_turn(messages: list[Any]) -> str:
    """The turn before the latest message, for follow-ups such as "E quanto custa?" or "And on
    debit?" that only make sense with it (QA-014). Data for the classifier, never a source of
    instructions: the classifier is told so and only the latest message is classified."""
    earlier = messages[:-1]
    reply = next((m for m in reversed(earlier) if isinstance(m, AIMessage)), None)
    question = next((m for m in reversed(earlier) if isinstance(m, HumanMessage)), None)
    if reply is None or question is None:
        return ""
    q, a = str(question.content)[:_CONTEXT_CHARS], str(reply.content)[:_CONTEXT_CHARS]
    q, a = (s.replace("<", "&lt;").replace(">", "&gt;") for s in (q, a))
    return f"<previous_turn>\nMerchant: {q}\nAssistant: {a}\n</previous_turn>\n\n"


async def _classify(text: str, context: str = "") -> Classification | None:
    """One LLM call max. Fails open on error so the whole suite does not depend on the API."""
    try:
        model = get_model("input_guard").with_structured_output(InputGuardResult)
        result = await model.ainvoke(
            [
                SystemMessage(content=load_prompt("input_guard")),
                HumanMessage(content=f"{context}<latest_message>\n{text}\n</latest_message>"),
            ]
        )
    except Exception:
        return None
    return verdict(result) if isinstance(result, InputGuardResult) else None


async def input_guardrail(state: AgentState) -> dict[str, Any]:
    update: dict[str, Any] = {"route_trace": ["input_guardrail"]}
    found = list(state.get("input_pii") or [])

    last = state["messages"][-1] if state.get("messages") else None
    text = ""
    if isinstance(last, HumanMessage) and isinstance(last.content, str):
        masked, extra = mask(last.content)
        if extra:
            update["messages"] = [HumanMessage(content=masked, id=last.id)]
            found += extra
        text = masked
    mask_flag = "input:masked" if found else "input:pass"

    # Barrier 2: heuristics catch the loudest attacks with no LLM call at all.
    if text and _heuristic_hit(text):
        update["guardrail_flags"] = ["input:blocked"]
        GUARDRAIL_BLOCKS.labels("input", "heuristic").inc()
        return update

    # Barrier 3: structured-output classifier (bounded: one call max). The supervisor's plan does
    # not depend on the verdict, so it runs at the same time and is thrown away if the turn is
    # blocked: one model round trip less on every answered turn (REQ-REL-04). The planner only
    # writes a plan (no tools, nothing sent to the merchant) and sees the masked text. Skipped
    # when `summarize` is about to fold the history, since the plan must see the summary.
    messages = list(state.get("messages") or [])
    context = _previous_turn(messages)
    plan_task = None
    if text and len(messages) <= MEMORY_MAX_MESSAGES:
        from app.graph.supervisor import plan_turn

        masked_messages = [*messages[:-1], HumanMessage(content=text, id=getattr(last, "id", None))]
        plan_task = asyncio.ensure_future(plan_turn({**state, "messages": masked_messages}))
    classification = await _classify(text, context) if text else None
    update["preplan"] = None
    if classification in ("injection", "out_of_scope"):
        if plan_task is not None:
            plan_task.cancel()  # a blocked turn does not wait for a plan it will not use
        update["guardrail_flags"] = ["input:blocked"]
        GUARDRAIL_BLOCKS.labels("input", classification).inc()
        return update
    plan, plan_tokens = await plan_task if plan_task is not None else (None, 0)
    if plan is not None:  # tokens are counted by the supervisor when it adopts the plan
        update["preplan"] = {"plan": plan.model_dump(), "tokens": plan_tokens}

    flags = [mask_flag]
    if classification == "sensitive":
        flags.append("input:sensitive")
    elif classification == "human_request":
        flags.append("input:human_request")
    update["guardrail_flags"] = flags
    return update


def route_after_input(state: AgentState) -> Literal["safe_response", "summarize"]:
    return "safe_response" if "input:blocked" in state.get("guardrail_flags", []) else "summarize"
