"""E-mail verification link (T038 / FR-002).

Every rejected link gets the same answer, 410 ``token_invalid`` (contract ``auth.yaml``): unknown,
tampered, expired, already used or minted for another purpose. Telling those apart would let a
caller probe which tokens exist. A valid link activates the account; it does not log in (the
merchant signs in next, with the password chosen at registration).
"""

from __future__ import annotations

import secrets

import httpx
import pytest
from pydantic import SecretStr

from app.auth import one_time_tokens, passwords, repo
from app.core.config import get_settings
from app.db.pool import Pool

pytestmark = pytest.mark.req("REQ-SEC-01")

EMAIL = "verificar@loja.example"
PASSWORD = f"pw-{secrets.token_urlsafe(18)}"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


async def _pending_account(db_pool: Pool) -> repo.AccountRow:
    account = await repo.create_account(
        db_pool, email=EMAIL, display_name="Loja Pendente", role="merchant", status="pending"
    )
    await repo.set_password(db_pool, account.id, passwords.hash_password(PASSWORD))
    return account


async def _verify(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post("/v1/auth/verify-email", json={"token": token})


def _assert_gone(response: httpx.Response) -> None:
    assert response.status_code == 410, response.text
    assert response.json()["error"]["code"] == "token_invalid"


async def test_valid_link_activates_the_account_and_login_then_works(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    account = await _pending_account(db_pool)
    login_body = {"email": EMAIL, "password": PASSWORD}
    assert (await client.post("/v1/auth/login", json=login_body)).status_code == 401

    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="verify_email")
    response = await _verify(client, token)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    refreshed = await repo.get_account_by_id(db_pool, account.id)
    assert refreshed is not None and refreshed.status == "active"
    login = await client.post("/v1/auth/login", json=login_body)
    assert login.status_code == 200
    assert "session" in client.cookies


async def test_a_used_link_is_gone(client: httpx.AsyncClient, db_pool: Pool) -> None:
    account = await _pending_account(db_pool)
    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="verify_email")
    assert (await _verify(client, token)).status_code == 200
    _assert_gone(await _verify(client, token))


async def test_an_expired_link_is_gone_and_does_not_activate(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    account = await _pending_account(db_pool)
    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="verify_email")
    async with db_pool.connection() as conn:
        await conn.execute(
            "UPDATE auth_one_time_tokens SET expires_at = now() - interval '1 minute'"
            " WHERE account_id = %s AND purpose = 'verify_email'",
            (account.id,),
        )
    _assert_gone(await _verify(client, token))
    refreshed = await repo.get_account_by_id(db_pool, account.id)
    assert refreshed is not None and refreshed.status == "pending"


@pytest.mark.parametrize("tamper", ["flip_last_char", "unknown_token"])
async def test_a_tampered_or_unknown_link_gets_the_same_answer(
    client: httpx.AsyncClient, db_pool: Pool, tamper: str
) -> None:
    account = await _pending_account(db_pool)
    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="verify_email")
    if tamper == "flip_last_char":
        forged = token[:-1] + ("A" if token[-1] != "A" else "B")
    else:
        forged = secrets.token_urlsafe(32)
    _assert_gone(await _verify(client, forged))
    # The real link still works: a failed guess does not burn it.
    assert (await _verify(client, token)).status_code == 200


async def test_a_reset_link_is_not_a_verification_link(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    account = await _pending_account(db_pool)
    reset_token = await one_time_tokens.mint(
        db_pool, account_id=account.id, purpose="password_reset"
    )
    _assert_gone(await _verify(client, reset_token))
