import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind
from prometheus_client import start_http_server
from redis.asyncio import Redis

from app.api import admin, auth_endpoints, chat, conversations, handoffs, health, me
from app.core.config import get_settings
from app.core.csrf_middleware import enforce_csrf
from app.core.errors import install_error_handlers
from app.db.migrate import migrate
from app.db.pool import close_pool, open_pool
from app.graph.builder import build_graph
from app.observability.logging import configure_logging, get_logger
from app.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS, initialise_label_sets
from app.observability.tracing import configure_tracing, current_trace_id, get_tracer
from app.ops.scheduler import start as start_scheduler
from app.tools.interfaces import TIMEOUT_SECONDS


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()
    configure_tracing(settings.otel_exporter_otlp_endpoint)
    pool = await open_pool(settings.database_url)
    await migrate(pool)
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    app.state.graph = build_graph(checkpointer)
    app.state.redis = Redis.from_url(settings.redis_url)
    app.state.scheduler_task = await start_scheduler(pool, app.state.graph)
    # Aquece o cross-encoder em background APENAS quando SKIP_RERANKER nao esta setado.
    # Em maquinas apertadas (ex.: 8GB RAM) o FlashRank + numpy inflam a api para alem do
    # mem_limit e o kernel mata o container (OOM 137). SKIP_RERANKER=1 deixa o retriever
    # cair direto no fallback RRF (ver retriever.search), que ainda devolve fontes
    # ordenadas por BM25 + densa com score 0.85.
    import os

    if os.environ.get("SKIP_RERANKER", "").lower() not in ("1", "true", "yes"):
        from app.rag import retriever as _retriever

        async def _warm() -> None:
            try:
                await asyncio.to_thread(_retriever.warm_reranker)
                get_logger().info("reranker_warmed")
            except Exception as exc:  # noqa: BLE001 — log e segue
                get_logger().warning("reranker_warm_failed", error=str(exc)[:200])

        app.state.warm_task = asyncio.create_task(_warm())
    else:
        get_logger().info("reranker_skipped_env")
    if settings.metrics_port:
        # Separate port, not published by compose: only Prometheus on the internal network scrapes.
        start_http_server(settings.metrics_port)
    try:
        yield
    finally:
        app.state.scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await app.state.scheduler_task
        await app.state.redis.aclose()
        await close_pool()


def create_app() -> FastAPI:
    # Swagger UI, ReDoc and /openapi.json map every route and schema: useful locally and in the
    # contract tests, not something to publish in production (QA-028). The committed contracts
    # live in specs/*/contracts/.
    docs = get_settings().app_env != "production"
    app = FastAPI(
        title="Getnet Multi-Agent Support API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    install_error_handlers(app)
    initialise_label_sets(sorted(TIMEOUT_SECONDS))

    # Starlette wraps middleware in reverse-registration order: the LAST added is the
    # outermost. We add enforce_csrf first and trace_requests second so trace_requests
    # runs outermost (binds trace_id before enforce_csrf can emit its 403 response).
    app.middleware("http")(enforce_csrf)

    @app.middleware("http")
    async def trace_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # The parent comes only from an incoming W3C `traceparent`; without one the request is a
        # new root, never a child of whatever span happens to be current in the server process.
        with get_tracer().start_as_current_span(
            "http.request",
            context=extract(dict(request.headers)),
            kind=SpanKind.SERVER,
            attributes={"http.request.method": request.method, "url.path": request.url.path},
        ) as root_span:
            trace_id = current_trace_id()
            request.state.trace_id = trace_id
            structlog.contextvars.bind_contextvars(trace_id=trace_id)
            started = time.perf_counter()
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
                return response
            finally:
                route = request.scope.get("route")
                path = getattr(route, "path", request.url.path)
                elapsed = time.perf_counter() - started
                root_span.set_attribute("http.route", path)
                root_span.set_attribute("http.response.status_code", status)
                HTTP_REQUESTS.labels(path, request.method, str(status)).inc()
                HTTP_LATENCY.labels(path).observe(elapsed)
                get_logger().info(
                    "request",
                    route=path,
                    method=request.method,
                    status=status,
                    latency_ms=round(elapsed * 1000),
                )
                structlog.contextvars.clear_contextvars()

    app.include_router(health.router)
    app.include_router(auth_endpoints.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(handoffs.router)
    app.include_router(me.router)
    app.include_router(admin.router)
    return app


app = create_app()
