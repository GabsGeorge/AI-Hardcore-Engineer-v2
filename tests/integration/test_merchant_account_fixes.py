"""Conta web de lojista: sessões, chat sem vínculo e CSRF com Bearer vazio (QA-021, QA-058,
QA-060)."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.auth import passwords, repo
from app.core.config import get_settings
from app.db.pool import Pool

PASSWORD = "lojista-senha-forte-7"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


async def _signed_in_merchant(
    client: httpx.AsyncClient, db_pool: Pool, email: str, *, link_to: str | None
) -> repo.AccountRow:
    account = await repo.create_account(
        db_pool, email=email, display_name="Loja", role="merchant", status="active"
    )
    await repo.set_password(db_pool, account.id, passwords.hash_password(PASSWORD))
    if link_to is not None:
        async with db_pool.connection() as conn:
            await conn.execute(
                "INSERT INTO merchants (user_id, trade_name, document_masked, plan,"
                " settlement_days, anticipation_enabled) VALUES"
                " (%s, 'Loja Teste', '**.***.***/0001-**', 'Get Smart', 1, true)"
                " ON CONFLICT (user_id) DO NOTHING",
                (link_to,),
            )
        linked, _ = await repo.link_merchant(
            db_pool, merchant_user_id=link_to, account_id=account.id
        )
        assert linked
    login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return account


@pytest.mark.req("REQ-SEC-01")
async def test_linked_merchant_sees_and_revokes_its_own_sessions(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    """QA-060: a lista vinha vazia porque buscava pelo users.id (cliente1988), não pela conta."""
    await _signed_in_merchant(client, db_pool, "sess@example.com", link_to="cliente1988")
    listed = await client.get("/v1/auth/sessions")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1 and rows[0]["current"] is True

    revoke = await client.request(
        "DELETE", "/v1/auth/sessions", headers={"X-CSRF-Token": client.cookies["csrf"]}
    )
    assert revoke.status_code == 204
    still = await client.get("/v1/auth/sessions")
    assert [r["current"] for r in still.json()] == [True]


@pytest.mark.req("REQ-SEC-01")
async def test_unlinked_merchant_chat_is_a_clear_409(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    """QA-021: sem estabelecimento não há users.id dono da conversa; antes era 500 (FK)."""
    await _signed_in_merchant(client, db_pool, "solto@example.com", link_to=None)
    headers = {"X-CSRF-Token": client.cookies["csrf"]}
    for path in ("/v1/chat", "/v1/chat/stream"):
        resp = await client.post(path, json={"message": "oi"}, headers=headers)
        assert resp.status_code == 409, path
        assert resp.json()["error"]["code"] == "merchant_not_linked"


@pytest.mark.req("REQ-SEC-01")
async def test_empty_bearer_does_not_skip_csrf(client: httpx.AsyncClient, db_pool: Pool) -> None:
    """QA-058: `Authorization: Bearer ` vazio pulava o CSRF e o cookie autenticava."""
    await _signed_in_merchant(client, db_pool, "csrf@example.com", link_to="cliente1988")
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": "a" * 32, "rating": 1},
        headers={"Authorization": "Bearer "},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"
    # Com o token CSRF certo, o cabeçalho inválido também não cai para o cookie.
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": "a" * 32, "rating": 1},
        headers={"Authorization": "Bearer ", "X-CSRF-Token": client.cookies["csrf"]},
    )
    assert resp.status_code == 401
