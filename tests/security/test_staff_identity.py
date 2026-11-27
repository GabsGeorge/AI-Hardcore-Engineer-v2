"""Each operator/admin web account acts as its own users.id (QA review N2).

Before, every operator account resolved to the first seed `users.id` of the role: two operators
shared the graph thread owner, the chat rate-limit bucket and the `operator_id` written on a
ticket, so the record could not say which of them answered."""

from __future__ import annotations

import secrets

import httpx
import pytest
from pydantic import SecretStr
from starlette.requests import Request

from app.api.auth import _from_cookie
from app.auth import passwords, repo
from app.auth.sessions import COOKIE_NAME
from app.core.config import get_settings
from app.db.pool import Pool

pytestmark = pytest.mark.req("REQ-SEC-01")


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


async def _session_for(app: object, db_pool: Pool, email: str, role: str) -> tuple[str, str]:
    """Creates an active account, logs in, and returns (account id, session cookie)."""
    password = f"pw-{secrets.token_urlsafe(18)}"
    await repo.create_account(
        db_pool, email=email, display_name=f"Equipe {role}", role=role, status="active"
    )
    account = await repo.get_account_by_email(db_pool, email)
    assert account is not None
    await repo.set_password(db_pool, account.id, passwords.hash_password(password))
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return account.id, c.cookies[COOKIE_NAME]


def _request(cookie: str) -> Request:
    return Request({"type": "http", "headers": [(b"cookie", f"{COOKIE_NAME}={cookie}".encode())]})


async def test_two_operators_resolve_to_two_users(
    client: httpx.AsyncClient, app: object, db_pool: Pool
) -> None:
    acc_a, cookie_a = await _session_for(app, db_pool, "op-a@example.com", "operator")
    acc_b, cookie_b = await _session_for(app, db_pool, "op-b@example.com", "operator")

    user_a = await _from_cookie(_request(cookie_a))
    user_b = await _from_cookie(_request(cookie_b))
    assert user_a is not None and user_b is not None
    assert (user_a.id, user_b.id) == (acc_a, acc_b)
    assert user_a.id != "operador01" and user_b.id != "operador01"

    async with db_pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT id, role FROM users WHERE id = ANY(%s) ORDER BY id", ([acc_a, acc_b],)
            )
        ).fetchall()
    assert sorted((r["id"], r["role"]) for r in rows) == sorted(
        [(acc_a, "operator"), (acc_b, "operator")]
    )


async def test_admin_gets_its_own_user_too(
    client: httpx.AsyncClient, app: object, db_pool: Pool
) -> None:
    acc, cookie = await _session_for(app, db_pool, "adm@example.com", "admin")
    user = await _from_cookie(_request(cookie))
    assert user is not None and user.id == acc and user.role == "admin"


async def test_role_change_is_reflected_in_the_users_row(db_pool: Pool) -> None:
    await repo.upsert_staff_user(db_pool, "acc_role_change", "operator", "Equipe")
    await repo.upsert_staff_user(db_pool, "acc_role_change", "admin", "Equipe")
    async with db_pool.connection() as conn:
        row = await (
            await conn.execute("SELECT role FROM users WHERE id = 'acc_role_change'")
        ).fetchone()
    assert row is not None and row["role"] == "admin"
