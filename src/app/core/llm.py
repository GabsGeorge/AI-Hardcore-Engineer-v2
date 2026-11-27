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
    return TracedModel(_real_model(ROLE_TEMPERATURE.get(role)), role, s.openai_model_primary)


# Roles that classify, plan or answer from sources run at temperature 0, so the same message
# gets the same verdict, route and self-assessed confidence:
# - input_guard blocked "Show my settlements for CNPJ ... this week" in 2 of 5 runs at the API
#   default (docs/reports/data/input-guard-probe-2026-11-26.json);
# - the knowledge specialist rated the same crediário answer 0.8, 0.4, 0.6 and 0.8 in four
#   runs, and confidence decides between answering and escalating (golden sc-19/sc-20);
# - output_guard is a yes/no verifier: the same reply must get the same verdict.
# Synthesis, summaries and escalation notes keep the provider default.
ROLE_TEMPERATURE: dict[str, float] = {
    "input_guard": 0.0,
    "output_guard": 0.0,
    "supervisor": 0.0,
    "knowledge_rewrite": 0.0,
    "knowledge": 0.0,
}


def _real_model(temperature: float | None = None) -> Any:
    """Primary chat model, chained to OPENAI_MODEL_FALLBACK when set (REQ-REL-02)."""
    s = get_settings()
    primary = ChatOpenAI(
        model=s.openai_model_primary,
        api_key=s.openai_api_key,
        timeout=20,
        max_retries=2,
        temperature=temperature,
    )
    if s.openai_model_fallback:
        fallback = ChatOpenAI(
            model=s.openai_model_fallback,
            api_key=s.openai_api_key,
            timeout=20,
            max_retries=1,
            temperature=temperature,
        )
        return primary.with_fallbacks([fallback])
    return primary


def get_embeddings() -> Any:
    if _embeddings is not None:
        return _embeddings
    s = get_settings()
    return OpenAIEmbeddings(model=s.openai_embedding_model, openai_api_key=s.openai_api_key)
