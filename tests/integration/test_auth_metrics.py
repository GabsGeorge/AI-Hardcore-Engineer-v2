"""Observability of the auth flow (T035): login counters and the ``auth.*`` spans.

The counter labels are the ones production uses (``ok``, ``bad_password``, ``unknown_account``,
the rate-limit decision or the account status), the same value the ``auth.login`` span carries
as ``auth.outcome``. The e-mail address never goes on a span.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from unittest.mock import patch

import aiosmtplib
import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY
from pydantic import SecretStr

from app.auth import passwords, repo
from app.core.config import get_settings
from app.db.pool import Pool

pytestmark = pytest.mark.req("REQ-OBS-02")

EMAIL = "metricas@loja.example"
PASSWORD = f"pw-{secrets.token_urlsafe(18)}"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), "tests need the SDK provider to record spans"
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


def _logins(outcome: str) -> float:
    return REGISTRY.get_sample_value("getnet_auth_login_total", {"outcome": outcome}) or 0.0


def _emails(metric: str, kind: str) -> float:
    return REGISTRY.get_sample_value(metric, {"kind": kind}) or 0.0


async def _active_account(db_pool: Pool) -> None:
    account = await repo.create_account(
        db_pool, email=EMAIL, display_name="Loja Métricas", role="merchant", status="active"
    )
    await repo.set_password(db_pool, account.id, passwords.hash_password(PASSWORD))


def _login_spans(exporter: InMemorySpanExporter) -> list[dict[str, object]]:
    return [
        dict(s.attributes or {}) for s in exporter.get_finished_spans() if s.name == "auth.login"
    ]


async def test_successful_login_counts_ok_and_opens_a_session(
    client: httpx.AsyncClient, db_pool: Pool, spans: InMemorySpanExporter
) -> None:
    await _active_account(db_pool)
    ok_before = _logins("ok")
    sessions_before = REGISTRY.get_sample_value("getnet_auth_sessions_opened_total") or 0.0

    r = await client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert r.status_code == 200
    assert _logins("ok") == ok_before + 1
    assert REGISTRY.get_sample_value("getnet_auth_sessions_opened_total") == sessions_before + 1
    assert _login_spans(spans) == [{"auth.outcome": "ok"}]


@pytest.mark.parametrize(
    ("email", "password", "outcome"),
    [
        (EMAIL, "senha-errada-" + secrets.token_hex(4), "bad_password"),
        ("ninguem@loja.example", PASSWORD, "unknown_account"),
    ],
)
async def test_failed_login_counts_the_reason(
    client: httpx.AsyncClient,
    db_pool: Pool,
    spans: InMemorySpanExporter,
    email: str,
    password: str,
    outcome: str,
) -> None:
    await _active_account(db_pool)
    before, ok_before = _logins(outcome), _logins("ok")

    r = await client.post("/v1/auth/login", json={"email": email, "password": password})

    assert r.status_code == 401
    assert _logins(outcome) == before + 1
    assert _logins("ok") == ok_before
    login_spans = _login_spans(spans)
    assert login_spans == [{"auth.outcome": outcome}]
    assert all(EMAIL not in str(v) for attrs in login_spans for v in attrs.values())


async def test_reset_request_emits_a_send_email_span_without_the_address(
    client: httpx.AsyncClient, db_pool: Pool, spans: InMemorySpanExporter
) -> None:
    await _active_account(db_pool)
    sent_before = _emails("getnet_auth_email_sent_total", "password_reset")

    async def _fake_send(*args: object, **kwargs: object) -> None:
        return None

    with patch.object(aiosmtplib, "send", side_effect=_fake_send):
        r = await client.post("/v1/auth/password/reset-request", json={"email": EMAIL})

    assert r.status_code == 202
    sent = [s for s in spans.get_finished_spans() if s.name == "auth.send_email"]
    assert len(sent) == 1
    attrs = dict(sent[0].attributes or {})
    assert attrs == {"email.kind": "password_reset", "email.delivered": True}
    assert _emails("getnet_auth_email_sent_total", "password_reset") == sent_before + 1


async def test_failed_delivery_is_recorded_on_the_span_and_the_error_counter(
    client: httpx.AsyncClient, db_pool: Pool, spans: InMemorySpanExporter
) -> None:
    await _active_account(db_pool)
    errors_before = _emails("getnet_auth_email_errors_total", "password_reset")

    async def _boom(*args: object, **kwargs: object) -> None:
        raise aiosmtplib.SMTPConnectError("mailpit down")

    with patch.object(aiosmtplib, "send", side_effect=_boom):
        r = await client.post("/v1/auth/password/reset-request", json={"email": EMAIL})

    assert r.status_code == 202  # best-effort delivery: the caller never learns about SMTP
    (sent,) = [s for s in spans.get_finished_spans() if s.name == "auth.send_email"]
    attrs = dict(sent.attributes or {})
    assert attrs["email.delivered"] is False and attrs["email.error"] == "SMTPConnectError"
    assert _emails("getnet_auth_email_errors_total", "password_reset") == errors_before + 1
