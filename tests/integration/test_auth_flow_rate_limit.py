"""Cadastro e recuperação de senha limitados por IP e por e-mail (E2, REQ-SEC-08).

5 por hora por IP e por e-mail; o 6º pedido recebe 429 com Retry-After e a mesma mensagem para
e-mail novo e existente (o limite não revela cadastro, FR-004).
"""

from __future__ import annotations

from unittest.mock import patch

import aiosmtplib
import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.db.pool import Pool

LIMIT = 5


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))
    monkeypatch.setattr(s, "auth_flow_limit_per_hour", LIMIT)


def _register(email: str) -> dict[str, str]:
    return {"display_name": "Loja", "email": email, "password": "senha-segura-demo"}


def _assert_limited(resp: httpx.Response) -> None:
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "rate_limited"
    retry_after = int(resp.headers["Retry-After"])
    assert 1 <= retry_after <= 3600


@pytest.mark.req("REQ-SEC-08")
async def test_register_is_limited_per_email(client: httpx.AsyncClient, db_pool: Pool) -> None:
    with patch.object(aiosmtplib, "send"):
        for i in range(LIMIT):
            # IPs diferentes: só o limite por e-mail pode disparar.
            resp = await client.post(
                "/v1/auth/register",
                json=_register("alvo@loja.example"),
                headers={"X-Real-IP": f"10.0.0.{i}"},
            )
            assert resp.status_code == 202
        _assert_limited(
            await client.post(
                "/v1/auth/register",
                json=_register("alvo@loja.example"),
                headers={"X-Real-IP": "10.0.0.99"},
            )
        )


@pytest.mark.req("REQ-SEC-08")
async def test_register_is_limited_per_ip(client: httpx.AsyncClient, db_pool: Pool) -> None:
    with patch.object(aiosmtplib, "send"):
        for i in range(LIMIT):
            resp = await client.post(
                "/v1/auth/register",
                json=_register(f"loja{i}@loja.example"),
                headers={"X-Real-IP": "203.0.113.7"},
            )
            assert resp.status_code == 202
        _assert_limited(
            await client.post(
                "/v1/auth/register",
                json=_register("outra@loja.example"),
                headers={"X-Real-IP": "203.0.113.7"},
            )
        )


@pytest.mark.req("REQ-SEC-08")
async def test_reset_request_is_limited_per_email_and_per_ip(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    with patch.object(aiosmtplib, "send"):
        for i in range(LIMIT):
            resp = await client.post(
                "/v1/auth/password/reset-request",
                json={"email": "alguem@loja.example"},
                headers={"X-Real-IP": f"10.1.0.{i}"},
            )
            assert resp.status_code == 202
        _assert_limited(
            await client.post(
                "/v1/auth/password/reset-request",
                json={"email": "alguem@loja.example"},
                headers={"X-Real-IP": "10.1.0.99"},
            )
        )
        for i in range(LIMIT):
            resp = await client.post(
                "/v1/auth/password/reset-request",
                json={"email": f"varredura{i}@loja.example"},
                headers={"X-Real-IP": "198.51.100.4"},
            )
            assert resp.status_code == 202
        _assert_limited(
            await client.post(
                "/v1/auth/password/reset-request",
                json={"email": "varredura-extra@loja.example"},
                headers={"X-Real-IP": "198.51.100.4"},
            )
        )


@pytest.mark.req("REQ-SEC-08")
async def test_limited_response_does_not_reveal_whether_the_email_exists(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    with patch.object(aiosmtplib, "send"):
        await client.post("/v1/auth/register", json=_register("existe@loja.example"))
        bodies = []
        for email in ("existe@loja.example", "nao-existe@loja.example"):
            for _ in range(LIMIT + 1):
                last = await client.post(
                    "/v1/auth/password/reset-request",
                    json={"email": email},
                    headers={"X-Real-IP": f"192.0.2.{len(bodies) + 1}"},
                )
            assert last.status_code == 429
            bodies.append(last.json()["error"]["message"])
    assert bodies[0] == bodies[1]
