"""Prometheus metrics (REQ-OBS-03). Define each metric here once; call sites import it."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "getnet_http_requests_total", "HTTP requests.", labelnames=("route", "method", "status")
)
HTTP_LATENCY = Histogram(
    "getnet_http_request_duration_seconds",
    "API route latency.",
    labelnames=("route",),
    buckets=(0.1, 0.3, 0.6, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 20.0),
)
# A chat turn from request to final answer, for /v1/chat AND /v1/chat/stream (the route the web
# UI uses). The HTTP histogram above stops when the stream starts, so the UI traffic was out of
# the latency SLO panel and alert (QA-078).
CHAT_TURN_LATENCY = Histogram(
    "getnet_chat_turn_duration_seconds",
    "Chat turn latency, request to final answer.",
    labelnames=("endpoint",),
    buckets=(0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 60.0),
)
# Where a turn's time goes (docs/limitations.md §11): one histogram per graph node and one per
# LLM role. Buckets cover a fast classifier call up to a node that waits on several calls.
_STAGE_BUCKETS = (0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0)
NODE_LATENCY = Histogram(
    "getnet_graph_node_duration_seconds",
    "Wall time of each graph node per turn.",
    labelnames=("node",),
    buckets=_STAGE_BUCKETS,
)
LLM_LATENCY = Histogram(
    "getnet_llm_call_duration_seconds",
    "Wall time of each LLM call per graph role.",
    labelnames=("role",),
    buckets=_STAGE_BUCKETS,
)
ROUTE_TAKEN = Counter("getnet_route_total", "Supervisor dispatches per node.", labelnames=("node",))
TOKENS = Counter("getnet_tokens_total", "LLM tokens per graph role.", labelnames=("role",))
TOOL_ERRORS = Counter("getnet_tool_errors_total", "Failed tool calls.", labelnames=("tool",))
GUARDRAIL_BLOCKS = Counter(
    "getnet_guardrail_blocks_total", "Guardrail blocks/rewrites.", labelnames=("stage", "kind")
)
HANDOFFS = Counter("getnet_handoffs_total", "Handoffs opened.", labelnames=("reason",))
AUTH_FAILURES = Counter("getnet_auth_failures_total", "Requests rejected with 401.")
AUTH_LOGIN = Counter("getnet_auth_login_total", "Login outcomes.", labelnames=("outcome",))
AUTH_EMAIL_SENT = Counter(
    "getnet_auth_email_sent_total", "Transactional e-mails sent.", labelnames=("kind",)
)
AUTH_EMAIL_ERRORS = Counter(
    "getnet_auth_email_errors_total", "Transactional e-mail errors.", labelnames=("kind",)
)
SESSIONS_ACTIVE = Counter("getnet_auth_sessions_opened_total", "Sessions opened via login.")
ONLINE_EVAL_SCORE = Histogram(
    "getnet_online_eval_score",
    "LLM-as-judge: faithfulness e relevance amostrados em produção (0-1).",
    labelnames=("metric",),
    buckets=(0.0, 0.3, 0.5, 0.7, 0.8, 0.9, 1.0),
)


def initialise_label_sets(tools: list[str]) -> None:
    """Create every known label set at 0, so a panel shows 0 instead of "No data" before the
    first event (QA-075..QA-077: tool errors, login outcomes, e-mails)."""
    for tool in tools:
        TOOL_ERRORS.labels(tool)
    for outcome in (
        "ok",
        "bad_password",
        "unknown_account",
        "locked",
        "rate_limited",
        "pending",
        "deactivated",
    ):
        AUTH_LOGIN.labels(outcome)
    for kind in ("verify_email", "password_reset", "invite"):
        AUTH_EMAIL_SENT.labels(kind)
        AUTH_EMAIL_ERRORS.labels(kind)
    for endpoint in ("/v1/chat", "/v1/chat/stream"):
        CHAT_TURN_LATENCY.labels(endpoint)
