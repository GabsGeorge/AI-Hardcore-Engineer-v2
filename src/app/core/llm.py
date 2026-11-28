"""Single entry point for chat models and embeddings, with an override hook for tests."""

from collections.abc import Callable
from typing import Any

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import get_settings

_model_factory: Callable[[str], Any] | None = None
_embeddings: Any | None = None


def set_overrides(
    model_factory: Callable[[str], Any] | None = None, embeddings: Any | None = None
) -> None:
    """Tests install fakes here (ADR 0006); passing None restores the real providers."""
    global _model_factory, _embeddings
    _model_factory, _embeddings = model_factory, embeddings


def get_model(role: str) -> Any:
    """Chat model for a graph role, wrapped so every call opens an ``llm.<role>`` span with model,
    tokens, latency and prompt version (REQ-OBS-02). Fakes installed by tests are wrapped too."""
    from app.observability.tracing import TracedModel

    s = get_settings()
    if _model_factory is not None:
        return TracedModel(_model_factory(role), role, s.openai_model_primary or "test-double")
    name = model_for(role)
    streaming = role in STREAM_ROLES
    return TracedModel(_real_model(ROLE_TEMPERATURE.get(role), name, streaming), role, name)


# The short checks around a turn (input classifier, output verifier, search rewrite) can run on
# another model (OPENAI_MODEL_CHECKS), for all of them or only the ones listed in
# OPENAI_MODEL_CHECKS_ROLES. Empty means the primary model: nothing changes until a candidate has
# been measured against the guard suites and the golden set.
CHECK_ROLES = frozenset({"input_guard", "output_guard", "knowledge_rewrite"})


# Roles whose text the merchant reads: `/v1/chat/stream` shows it while it is generated
# (app/api/token_stream.py). Every other role keeps the plain request/response call even when a
# streaming consumer is attached, so classifiers, planner and verifier behave exactly as in
# `/v1/chat`.
STREAM_ROLES = frozenset({"knowledge", "support", "synthesize"})


def model_for(role: str) -> str:
    s = get_settings()
    if s.openai_model_checks and role in CHECK_ROLES:
        chosen = {r.strip() for r in s.openai_model_checks_roles.split(",") if r.strip()}
        if role in chosen:
            return s.openai_model_checks
    return s.openai_model_primary


def _is_reasoning_model(name: str) -> bool:
    """gpt-5 and o-series models take `reasoning_effort` and reject `temperature`."""
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


# Roles that classify, plan or answer from sources run at temperature 0, so the same message
# gets the same verdict, route and self-assessed confidence:
# - input_guard blocked "Show my settlements for CNPJ ... this week" in 2 of 5 runs at the API
#   default (docs/reports/data/input-guard-probe-2026-11-26.json);
# - the knowledge specialist rated the same crediário answer 0.8, 0.4, 0.6 and 0.8 in four
#   runs, and confidence decides between answering and escalating (golden sc-19/sc-20);
# - output_guard is a yes/no verifier: the same reply must get the same verdict;
# - support answers only from tool results (it invented generic troubleshooting steps when
#   diagnose_terminal returned an empty list).
# Synthesis, summaries and escalation notes keep the provider default.
ROLE_TEMPERATURE: dict[str, float] = {
    "input_guard": 0.0,
    "output_guard": 0.0,
    "supervisor": 0.0,
    "knowledge_rewrite": 0.0,
    "knowledge": 0.0,
    "support": 0.0,
}


def _real_model(
    temperature: float | None = None, model: str | None = None, streaming: bool = False
) -> Any:
    """Chat model for a role, chained to OPENAI_MODEL_FALLBACK when set (REQ-REL-02).
    ``streaming=False`` disables token streaming for the model even under a streaming consumer;
    ``stream_usage`` keeps the token count (budget, metrics) on streamed calls."""
    s = get_settings()
    name = model or s.openai_model_primary
    extra: dict[str, Any] = {}
    if _is_reasoning_model(name):
        # Fast mode for the short checks; these models do not accept a temperature.
        extra["reasoning_effort"] = "minimal"
        temperature = None
    primary = ChatOpenAI(
        model=name,
        api_key=s.openai_api_key,
        timeout=20,
        max_retries=2,
        temperature=temperature,
        disable_streaming=not streaming,
        stream_usage=True,
        **extra,
    )
    if s.openai_model_fallback:
        fallback = ChatOpenAI(
            model=s.openai_model_fallback,
            api_key=s.openai_api_key,
            timeout=20,
            max_retries=1,
            temperature=None if _is_reasoning_model(s.openai_model_fallback) else temperature,
            disable_streaming=not streaming,
            stream_usage=True,
        )
        return primary.with_fallbacks([fallback])
    return primary


def get_embeddings() -> Any:
    if _embeddings is not None:
        return _embeddings
    s = get_settings()
    return OpenAIEmbeddings(model=s.openai_embedding_model, openai_api_key=s.openai_api_key)
