"""Knowledge specialist: answers about Getnet products with citations, or says it does not know.

1. `knowledge_rewrite` model → PT/EN query variants and whether the question is general.
2. `search_knowledge_base` for the question and each variant (hybrid retrieval, ADR 0003); hits
   are merged by chunk keeping the best reranked score.
3. Relevant hits → `knowledge` model answers from them. Otherwise a general question goes to
   `web_search`; anything else gets an honest "not found" without calling the model
   (REQ-RAG-07). Retrieved text is data inside `<untrusted_context>` (REQ-GR-05).
Confidence = min(self-assessment, objective signal) (research R-07).
"""

import asyncio
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agents.common import call_tool, make_result
from app.core.config import get_settings
from app.core.llm import get_model
from app.core.prompts import load_prompt
from app.graph.state import Source, SpecialistInput, ToolCallRecord
from app.rag.retriever import TOP_K, KbHit
from app.tools.interfaces import WebResult
from app.tools.knowledge_tools import search_knowledge_base, web_search

NOT_FOUND = {
    "pt": "Não encontrei essa informação nas fontes da Getnet que consultei, então prefiro não "
    "responder sem base. Você pode reformular a pergunta ou pedir para falar com um atendente.",
    "en": "I couldn't find this information in the Getnet sources I checked, so I'd rather not "
    "answer without a basis. You can rephrase the question or ask to talk to a human agent.",
}
UNAVAILABLE = {
    "pt": "Não consegui consultar as fontes de informação agora. Tente novamente em instantes "
    "ou peça para falar com um atendente.",
    "en": "I couldn't reach the information sources right now. Please try again shortly or ask "
    "to talk to a human agent.",
}
_REWRITE_MARKER = "<!-- query-rewrite -->"


class QueryVariant(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    lang: Literal["pt", "en"]


class QueryRewrite(BaseModel):
    """Structured output of the `knowledge_rewrite` role."""

    variants: list[QueryVariant] = Field(default_factory=list, max_length=3)
    general: bool = Field(
        default=False, description="public topic not about Getnet (weather, exchange rates)"
    )


class KnowledgeAnswer(BaseModel):
    """Structured output of the `knowledge` role."""

    answer: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "how well the context supports the answer YOU WROTE: 0.8-1.0 when it states the "
            "requested figures or steps from the context; 0.4-0.7 when it covers only part; "
            "at most 0.2 only when the answer says the information was not found"
        ),
    )
    cited: list[int] = Field(default_factory=list, description="numbers of the items used")


def _prompts() -> tuple[str, str]:
    answer, rewrite = load_prompt("knowledge").split(_REWRITE_MARKER)
    return answer.strip(), rewrite.strip()


def _tokens(raw: Any) -> int:
    usage = getattr(raw, "usage_metadata", None) or {}
    return int(usage.get("total_tokens", 0))


async def _structured(
    role: str, schema: type[BaseModel], system: str, human: str
) -> tuple[Any, int]:
    """(parsed or None, tokens). A failing model is an outcome, never an exception."""
    model = get_model(role).with_structured_output(schema, include_raw=True)
    try:
        out = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=human)])
    except Exception:
        return None, 0
    parsed = out.get("parsed")
    return (parsed if isinstance(parsed, schema) else None), _tokens(out.get("raw"))


SearchCall = tuple[list[KbHit] | None, ToolCallRecord]


async def _search_each(
    queries: list[tuple[str, str | None]], config: RunnableConfig
) -> list[SearchCall]:
    """One `search_knowledge_base` call per query, in parallel."""
    return list(
        await asyncio.gather(
            *(
                call_tool(
                    "knowledge",
                    "search_knowledge_base",
                    search_knowledge_base,
                    {"query": q, "lang": lang},
                    config,
                )
                for q, lang in queries
            )
        )
    )


async def _search(
    queries: list[tuple[str, str | None]],
    config: RunnableConfig,
    prefetched: list[SearchCall] | None = None,
) -> tuple[list[KbHit], list[ToolCallRecord], bool]:
    """Best TOP_K hits over all queries (plus calls already made), tool records, and whether any
    search worked."""
    calls = [*(prefetched or []), *(await _search_each(queries, config) if queries else [])]
    hits = merge_hits([output or [] for output, _ in calls])
    return hits, [record for _, record in calls], any(r["status"] == "ok" for _, r in calls)


def merge_hits(per_query: list[list[KbHit]], k: int = TOP_K) -> list[KbHit]:
    """Top-k over all query variants, where each variant first contributes its best passage.

    Sorting the union by score alone let one strong topic fill the five slots: for "Quais as
    taxas do Pix para PJ?" the Pix pages pushed out the MDR page that the `taxa por transação`
    variant had found, and the answer said "não encontrei" (golden sc-02).
    """
    best: dict[int, KbHit] = {}
    for hits in per_query:
        for hit in hits:
            if hit.chunk_id not in best or hit.score > best[hit.chunk_id].score:
                best[hit.chunk_id] = hit
    leaders = {hits[0].chunk_id for hits in per_query if hits}
    first = sorted((best[c] for c in leaders), key=lambda h: h.score, reverse=True)[:k]
    rest = sorted(
        (h for c, h in best.items() if c not in leaders), key=lambda h: h.score, reverse=True
    )
    return sorted([*first, *rest][:k], key=lambda h: h.score, reverse=True)


def _as_data(text: str) -> str:
    """Escapes angle brackets so retrieved text can never form (or reassemble) a tag."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _context(items: list[tuple[str, str]]) -> str:
    """Numbered items, escaped so retrieved text cannot close the block (REQ-GR-05)."""
    body = "\n\n".join(
        f"[{i}] {_as_data(header)}\n{_as_data(text)}"
        for i, (header, text) in enumerate(items, start=1)
    )
    return f"<untrusted_context>\n{body}\n</untrusted_context>"


def _cited(reply: KnowledgeAnswer, count: int) -> list[int]:
    """0-based indexes of the cited items; none valid → every item given to the model."""
    valid = sorted({n - 1 for n in reply.cited if 1 <= n <= count})
    return valid or list(range(count))


# A fee, rate or price question: its answer is on the MDR page, which only a query naming the
# per-transaction fee finds. The rewrite prompt always writes that query; the planner did not
# (for "Quais as taxas do Pix para pessoa jurídica?" it wrote 0 of 3 times, and the answer said
# Pix has no per-transaction cost, which the MDR page contradicts). These keep the rewrite.
_FEE_QUESTION = re.compile(
    r"\b(?:taxas?|tarifas?|custos?|custa[mr]?|pre[çc]os?|mdr|fees?|rates?|prices?|pricing|costs?)\b",
    re.IGNORECASE,
)

_MARKER = re.compile(r"\[(\d{1,3})\]")
_REPEATED_MARKER = re.compile(r"(\[\d{1,3}\])(?:\s*\1)+")


def renumber_citations(
    answer: str, item_sources: list[Source], cited: list[int]
) -> tuple[str, list[Source]]:
    """Citation markers point at the numbered passages the model saw, several of which may come
    from the same page; the merchant sees one entry per page. The source list is rebuilt from the
    markers in the order they first appear (then the remaining ``cited`` items), one entry per
    URL, and every marker is renumbered to its entry, so ``[n]`` is always the n-th listed source
    (QA-017). A marker outside the passages is dropped instead of pointing at nothing."""
    order: list[str] = []
    by_url: dict[str, Source] = {}

    def position(index: int) -> int:
        source = item_sources[index]
        if source["url"] not in by_url:
            by_url[source["url"]] = source
            order.append(source["url"])
        return order.index(source["url"]) + 1

    def replace(match: re.Match[str]) -> str:
        n = int(match.group(1))
        return f"[{position(n - 1)}]" if 1 <= n <= len(item_sources) else ""

    text = _MARKER.sub(replace, answer)
    for index in cited:
        position(index)
    text = _REPEATED_MARKER.sub(r"\1", text)
    return text, [by_url[url] for url in order]


async def knowledge_node(state: SpecialistInput, config: RunnableConfig) -> dict[str, Any]:
    subtask = state["subtask"]
    question = subtask["query"]
    language = state["language"]
    fixed = language if language in NOT_FOUND else "en"
    answer_prompt, rewrite_prompt = _prompts()
    answer_system = answer_prompt.replace("{language}", language)

    raw_query: tuple[str, str | None] = (question[:500], None)
    planned = [
        QueryVariant.model_validate(q)
        for q in subtask.get("search_queries") or []
        if not _FEE_QUESTION.search(question)
    ]
    raw_task: asyncio.Future[list[SearchCall]] | None = None
    if planned:
        # The supervisor already wrote the search queries in its plan: one LLM call less on the
        # critical path of every knowledge turn (docs/limitations.md §11).
        rewrite, tokens = QueryRewrite(variants=planned), 0
    else:
        # The search for the question as written does not depend on the rewrite: it runs while
        # the rewrite model answers, and only the variants are searched afterwards.
        raw_task = asyncio.ensure_future(_search_each([raw_query], config))
        rewrite_reply, tokens = await _structured(
            "knowledge_rewrite", QueryRewrite, rewrite_prompt, f"Question:\n{question}"
        )
        rewrite = rewrite_reply or QueryRewrite()
    # Allowlist restrita de topicos gerais que o merchant pode legitimamente perguntar
    # (clima + cambio). O enunciado so cobre esses dois casos via web_search; qualquer
    # outro assunto fora de Getnet deve cair em out_of_scope mesmo que o guardrail
    # tenha falhado (defense-in-depth). Antes o agente respondia sobre futebol,
    # politica, celebridades etc, quebrando escopo e seguranca.
    _general_keywords = (
        "clima",
        "tempo",
        "previsao",
        "previsão",
        "weather",
        "forecast",
        "cambio",
        "câmbio",
        "cotacao",
        "cotação",
        "euro",
        "dolar",
        "dólar",
        "dollar",
        "exchange",
        "currency",
    )
    question_lower = question.lower()
    is_allowed_general = any(k in question_lower for k in _general_keywords)
    if not rewrite.general and is_allowed_general:
        rewrite = QueryRewrite(variants=rewrite.variants, general=True)
    elif rewrite.general and not is_allowed_general:
        # Modelo marcou como geral mas nao bate com a allowlist: forca general=False
        # para nao chamar web_search em assunto fora de escopo.
        rewrite = QueryRewrite(variants=rewrite.variants, general=False)
    queries: list[tuple[str, str | None]] = list(
        dict.fromkeys([raw_query, *((v.query, v.lang) for v in rewrite.variants)])
    )
    if raw_task is not None:
        hits, tools, kb_ok = await _search(queries[1:], config, await raw_task)
    else:
        hits, tools, kb_ok = await _search(queries, config)
    threshold = get_settings().rag_relevance_threshold
    relevant = [h for h in hits if h.score >= threshold]

    def done(
        answer: str = "",
        confidence: float = 0.0,
        sources: list[Source] | None = None,
        error: str | None = None,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        result = make_result(
            subtask,
            "knowledge",
            answer=answer,
            confidence=confidence,
            sources=sources or [],
            tools_used=tools,
            error=error,
            evidence=evidence or [],
        )
        return {"results": [result], "route_trace": ["knowledge"], "tokens_used": tokens}

    # Perguntas publicas (clima, cambio) sempre via web_search: KB tem so conteudo Getnet e
    # o fallback RRF forca scores artificiais, entao hits de maquininha/Pix apareciam como
    # 'relevantes' para uma pergunta de clima e o modelo tentava responder com material
    # errado. Com general=True, pula direto para a rota de web_search.
    if relevant and not rewrite.general:
        items = [(f"{h.title} — {h.section} ({h.url})", h.content) for h in relevant]
        reply, used = await _structured(
            "knowledge",
            KnowledgeAnswer,
            answer_system,
            f"Question:\n{question}\n\n{_context(items)}",
        )
        tokens += used
        if reply is None:
            return done(error="model_error")
        kb_items: list[Source] = [
            {
                "title": h.title,
                "url": h.url,
                "kind": "kb",
                "retrieved_at": h.fetched_at.isoformat(),
            }
            for h in relevant
        ]
        kb_answer, kb_sources = renumber_citations(
            reply.answer, kb_items, _cited(reply, len(relevant))
        )
        return done(
            kb_answer,
            min(reply.confidence, relevant[0].score),
            kb_sources,
            evidence=[f"{header}\n{text}" for header, text in items],
        )

    if not rewrite.general:
        return done((NOT_FOUND if kb_ok else UNAVAILABLE)[fixed], hits[0].score if hits else 0.0)

    output, record = await call_tool(
        "knowledge", "web_search", web_search, {"query": question[:300]}, config
    )
    tools.append(record)
    if record["status"] != "ok":
        return done(UNAVAILABLE[fixed])
    results: list[WebResult] = output or []
    if not results:
        return done(NOT_FOUND[fixed])
    items = [
        (f"{r.title} ({r.url}), retrieved_at {r.retrieved_at.isoformat()}", r.snippet)
        for r in results
    ]
    reply, used = await _structured(
        "knowledge", KnowledgeAnswer, answer_system, f"Question:\n{question}\n\n{_context(items)}"
    )
    tokens += used
    if reply is None:
        return done(error="model_error")
    web_items: list[Source] = [
        {
            "title": r.title,
            "url": r.url,
            "kind": "web",
            "retrieved_at": r.retrieved_at.isoformat(),
        }
        for r in results
    ]
    web_answer, web_sources = renumber_citations(
        reply.answer, web_items, _cited(reply, len(results))
    )
    return done(
        web_answer,
        reply.confidence,
        web_sources,
        evidence=[f"{header}\n{text}" for header, text in items],
    )
