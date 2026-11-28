"""Password reset (T040 / FR-006): the new password replaces the old one, every live session is
revoked, and the same link cannot be used twice."""

from __future__ import annotations

import secrets

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.auth import one_time_tokens, passwords, repo
from app.core.config import get_settings
from app.db.pool import Pool

pytestmark = pytest.mark.req("REQ-SEC-01")

EMAIL = "reset@loja.example"
OLD = f"antiga-{secrets.token_urlsafe(16)}"
NEW = f"nova-{secrets.token_urlsafe(16)}"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


async def _account(db_pool: Pool) -> repo.AccountRow:
    account = await repo.create_account(
        db_pool, email=EMAIL, display_name="Loja Reset", role="merchant", status="active"
    )
    await repo.set_password(db_pool, account.id, passwords.hash_password(OLD))
    return account


def _browser(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(client: httpx.AsyncClient, password: str) -> httpx.Response:
    return await client.post("/v1/auth/login", json={"email": EMAIL, "password": password})


async def _live_sessions(db_pool: Pool, account_id: str) -> int:
    async with db_pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) AS n FROM auth_sessions"
                " WHERE account_id = %s AND revoked_at IS NULL",
                (account_id,),
            )
        ).fetchone()
    assert row is not None
    return int(row["n"])


async def test_reset_swaps_the_password_revokes_all_sessions_and_burns_the_link(
    client: httpx.AsyncClient, app: FastAPI, db_pool: Pool
) -> None:
    account = await _account(db_pool)
    # Two live sessions, as two browsers of the same merchant.
    async with _browser(app) as laptop, _browser(app) as phone:
        for browser in (laptop, phone):
            assert (await _login(browser, OLD)).status_code == 200
            assert (await browser.get("/v1/auth/me")).status_code == 200
        assert await _live_sessions(db_pool, account.id) == 2

        token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="password_reset")
        # The link is opened from the e-mail, without a session (`client` has none).
        reset = await client.post(
            "/v1/auth/password/reset", json={"token": token, "new_password": NEW}
        )
        assert reset.status_code == 200
        assert reset.json() == {"status": "ok"}

        for browser in (laptop, phone):
            assert (await browser.get("/v1/auth/me")).status_code == 401
    assert await _live_sessions(db_pool, account.id) == 0

    assert (await _login(client, OLD)).status_code == 401
    assert (await _login(client, NEW)).status_code == 200

    # Same link again, from a browser without a session: gone, and the password stays NEW.
    client.cookies.clear()
    again = await client.post(
        "/v1/auth/password/reset",
        json={"token": token, "new_password": f"terceira-{secrets.token_urlsafe(16)}"},
    )
    assert again.status_code == 410
    assert again.json()["error"]["code"] == "token_invalid"
    assert (await _login(client, NEW)).status_code == 200


async def test_a_weak_new_password_keeps_the_old_one_and_the_link(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    """A too-short password is rejected before the link is consumed: the old password keeps
    working and the same link still completes the reset (it used to be spent by the 422)."""
    account = await _account(db_pool)
    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="password_reset")
    weak = await client.post(
        "/v1/auth/password/reset", json={"token": token, "new_password": "curta"}
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "weak_password"
    assert (await _login(client, OLD)).status_code == 200

    client.cookies.clear()
    retry = await client.post("/v1/auth/password/reset", json={"token": token, "new_password": NEW})
    assert retry.status_code == 200
    assert (await _login(client, NEW)).status_code == 200
