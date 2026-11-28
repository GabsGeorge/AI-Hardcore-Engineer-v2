"""OpenTelemetry setup and the span helpers used by the graph (REQ-OBS-02).

Spans are always created (so every request has a W3C trace_id); they are exported over OTLP only
when OTEL_EXPORTER_OTLP_ENDPOINT is set (profile `obs` points it at Grafana Tempo).

Span tree of one chat turn (node spans come from ``traced_node``, ``llm.<role>`` from
``TracedModel``, ``tool.<name>`` from ``call_tool``, ``retrieval.*`` from the retriever):

    http.request
    └─ fastapi.endpoint                      (emitted by FastAPI itself)
       ├─ input_guardrail ── llm.input_guard
       ├─ supervisor ── supervisor.plan ── llm.supervisor
       ├─ knowledge ── llm.knowledge_rewrite, llm.knowledge
       │            └─ tool.search_knowledge_base ── retrieval.search
       │                 ├─ retrieval.bm25, retrieval.rerank   (hybrid_rerank only)
       │                 └─ retrieval.dense ── retrieval.embedding
       ├─ support ── llm.support, tool.<name>
       ├─ escalation ── llm.escalation
       ├─ synthesize ── llm.synthesize
       └─ output_guardrail ── llm.output_guard
"""

from __future__ import annotations

import contextlib
import inspect
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

_configured = False

SERVICE_NAME = "getnet-support-api"

# Graph role → prompt file under /prompts whose version the LLM span carries (ADR 0009).
ROLE_PROMPTS: dict[str, str] = {
    "supervisor": "supervisor",
    "knowledge": "knowledge",
    "knowledge_rewrite": "knowledge",
    "support": "support",
    "escalation": "escalation",
    "synthesize": "synthesize",
    "summarize": "summarize",
    "input_guard": "input_guard",
    "output_guard": "grounding",
    "online_eval": "online_eval",
}


def configure_tracing(otlp_endpoint: str) -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))
        )
    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("getnet-support")


def current_trace_id() -> str:
    return format(trace.get_current_span().get_span_context().trace_id, "032x")


def set_prompt_version(span: trace.Span, prompt_name: str) -> None:
    """Anota o span com ``prompt.version`` do prompt carregado (REQ-OBS-02, ADR 0009)."""
    from app.core.prompts import prompt_version

    span.set_attribute("prompt.name", prompt_name)
    span.set_attribute("prompt.version", prompt_version(prompt_name))


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Child span of the current one; ``None`` attribute values are skipped."""
    with get_tracer().start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current


# --- Graph nodes ---------------------------------------------------------------------------


def _is_interrupt(exc: BaseException) -> bool:
    """LangGraph pauses a node (HITL) by raising GraphInterrupt: a pause, not an error."""
    return type(exc).__name__ in ("GraphInterrupt", "NodeInterrupt", "ParentCommand")


def traced_node(name: str, fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Wraps a graph node in a span named after the node (``graph.node`` attribute).

    The wrapper keeps the ``(state, config)`` shape LangGraph inspects; ``config`` is only passed
    on to nodes that declare it."""
    wants_config = "config" in inspect.signature(fn).parameters
    tracer = get_tracer()

    # LangGraph only injects `config` into a parameter annotated as RunnableConfig.
    async def node(state: Any, config: RunnableConfig) -> Any:
        from app.observability.metrics import NODE_LATENCY

        with tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as current:
            current.set_attribute("graph.node", name)
            started = time.perf_counter()
            try:
                result = await (fn(state, config) if wants_config else fn(state))
                NODE_LATENCY.labels(name).observe(time.perf_counter() - started)
                return result
            except BaseException as exc:
                if _is_interrupt(exc):
                    current.set_attribute("graph.interrupted", True)
                else:
                    current.record_exception(exc)
                    current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    node.__name__ = node.__qualname__ = getattr(fn, "__name__", name)
    node.__doc__ = fn.__doc__
    return node


# --- LLM calls -----------------------------------------------------------------------------


def _usage(result: Any) -> tuple[Any, dict[str, Any]]:
    """(message carrying usage, usage dict) for an AIMessage or an ``include_raw`` dict."""
    message = result.get("raw") if isinstance(result, dict) else result
    return message, dict(getattr(message, "usage_metadata", None) or {})


def _with_role(config: Any, role: str) -> Any:
    """The call's config plus ``metadata.llm_role``. LangGraph hands this metadata to whoever
    streams the turn (``stream_mode="messages"``), which is how `/v1/chat/stream` knows a chunk
    is the specialist's answer and not the classifier, the planner or the verifier. The config in
    context (callbacks, graph metadata) is kept: only the role is added."""
    from langchain_core.runnables.config import ensure_config, merge_configs

    return merge_configs(ensure_config(config), {"metadata": {"llm_role": role}})


class TracedModel:
    """Chat-model proxy that opens one ``llm.<role>`` span per call (REQ-OBS-02).

    Attributes: ``llm.role``, ``gen_ai.request.model``, ``gen_ai.response.model``,
    ``gen_ai.usage.input_tokens``, ``gen_ai.usage.output_tokens``, ``llm.latency_ms``,
    ``prompt.name`` and ``prompt.version``. Tokens also feed ``getnet_tokens_total{role}``.

    ``with_structured_output`` always asks the inner model for ``include_raw=True`` so the token
    usage is visible, and unwraps the result when the caller did not ask for the raw message
    (re-raising the parsing error exactly as ``include_raw=False`` would)."""

    def __init__(self, inner: Any, role: str, model: str = "", unwrap: bool = False) -> None:
        self._inner, self._role, self._model, self._unwrap = inner, role, model, unwrap

    @property
    def inner(self) -> Any:
        """The wrapped model (a ChatOpenAI, its fallback chain, or a test fake)."""
        return self._inner

    def with_structured_output(
        self, schema: Any, *, include_raw: bool = False, **kwargs: Any
    ) -> TracedModel:
        inner = self._inner.with_structured_output(schema, include_raw=True, **kwargs)
        return TracedModel(inner, self._role, self._model, unwrap=not include_raw)

    def bind_tools(self, *args: Any, **kwargs: Any) -> TracedModel:
        return TracedModel(self._inner.bind_tools(*args, **kwargs), self._role, self._model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def ainvoke(self, payload: Any, config: Any = None, **kwargs: Any) -> Any:
        from app.observability.metrics import LLM_LATENCY, TOKENS

        with get_tracer().start_as_current_span(f"llm.{self._role}") as current:
            current.set_attribute("llm.role", self._role)
            current.set_attribute("gen_ai.system", "openai")
            if self._model:
                current.set_attribute("gen_ai.request.model", self._model)
            prompt = ROLE_PROMPTS.get(self._role)
            if prompt:
                # A role whose prompt file is missing still gets its span.
                with contextlib.suppress(OSError):
                    set_prompt_version(current, prompt)
            started = time.perf_counter()
            try:
                result = await self._inner.ainvoke(
                    payload, _with_role(config, self._role), **kwargs
                )
            finally:
                elapsed = time.perf_counter() - started
                current.set_attribute("llm.latency_ms", round(elapsed * 1000, 1))
                LLM_LATENCY.labels(self._role).observe(elapsed)
            message, usage = _usage(result)
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            current.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            current.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            response_model = (getattr(message, "response_metadata", None) or {}).get("model_name")
            if response_model:
                current.set_attribute("gen_ai.response.model", str(response_model))
            total = int(usage.get("total_tokens", input_tokens + output_tokens))
            if total:
                TOKENS.labels(self._role).inc(total)
            if not self._unwrap:
                return result
            if result.get("parsing_error") is not None:
                raise result["parsing_error"]
            return result.get("parsed")
