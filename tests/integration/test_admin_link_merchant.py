"""Vincula conta → merchant (T085 / FR-021). O constraint UNIQUE em ``merchants.account_id``
garante que um estabelecimento fica em no máximo uma conta."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.auth import passwords, repo
from app.core.config import get_settings
from app.db.pool import Pool


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


async def _login_as_admin(client: httpx.AsyncClient, db_pool: Pool) -> None:
    await repo.create_account(
        db_pool,
        email="admin@getnet.example",
        display_name="Admin",
        role="admin",
        status="active",
    )
    await repo.set_password(
        db_pool,
        (await repo.get_account_by_email(db_pool, "admin@getnet.example")).id,  # type: ignore[union-attr]
        passwords.hash_password("admin-senha-forte-123"),
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": "admin@getnet.example", "password": "admin-senha-forte-123"},
    )
    assert r.status_code == 200


async def _merchant_account(db_pool: Pool, email: str) -> repo.AccountRow:
    acc = await repo.create_account(
        db_pool, email=email, display_name="Loja", role="merchant", status="active"
    )
    await repo.set_password(db_pool, acc.id, passwords.hash_password("lojista-senha-forte-7"))
    return acc


async def _seed_merchant(db_pool: Pool, user_id: str) -> None:
    async with db_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO merchants (user_id, trade_name, document_masked, plan,"
            " settlement_days, anticipation_enabled) VALUES"
            " (%s, 'Loja Teste', '**.***.***/0001-**', 'Get Smart', 1, true)"
            " ON CONFLICT (user_id) DO NOTHING",
            (user_id,),
        )


@pytest.mark.req("REQ-API-02")
async def test_link_and_second_link_conflicts_then_unlink_works(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    await _login_as_admin(client, db_pool)
    acc1 = await _merchant_account(db_pool, "lojista1@example.com")
    acc2 = await _merchant_account(db_pool, "lojista2@example.com")
    await _seed_merchant(db_pool, "cliente1988")
    csrf = client.cookies["csrf"]

    first = await client.post(
        "/v1/admin/merchants/cliente1988/link",
        json={"account_id": acc1.id},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 204

    second = await client.post(
        "/v1/admin/merchants/cliente1988/link",
        json={"account_id": acc2.id},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "merchant_already_linked"

    unlink = await client.request(
        "DELETE",
        "/v1/admin/merchants/cliente1988/link",
        headers={"X-CSRF-Token": csrf},
    )
    assert unlink.status_code == 204

    # Agora o segundo vínculo passa.
    third = await client.post(
        "/v1/admin/merchants/cliente1988/link",
        json={"account_id": acc2.id},
        headers={"X-CSRF-Token": csrf},
    )
    assert third.status_code == 204


@pytest.mark.req("REQ-API-02")
async def test_self_registered_merchant_links_to_seed_establishment(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    """QA-003: o cadastro já cria um estabelecimento demo (wusr_*) com o mesmo account_id;
    o vínculo com cliente1988 libera esse estabelecimento em vez de dar 500 (UNIQUE)."""
    await _login_as_admin(client, db_pool)
    acc = await _merchant_account(db_pool, "auto@example.com")
    own = await repo.create_merchant_for_account(db_pool, account_id=acc.id, display_name="Loja")
    await _seed_merchant(db_pool, "cliente1988")

    r = await client.post(
        "/v1/admin/merchants/cliente1988/link",
        json={"account_id": acc.id},
        headers={"X-CSRF-Token": client.cookies["csrf"]},
    )
    assert r.status_code == 204
    assert await repo.merchant_user_id_for(db_pool, acc.id) == "cliente1988"
    async with db_pool.connection() as conn:
        row = await (
            await conn.execute("SELECT account_id FROM merchants WHERE user_id = %s", (own,))
        ).fetchone()
        audit = await (
            await conn.execute(
                "SELECT metadata FROM audit_events WHERE action = 'merchant_linked'"
                " ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
    assert row is not None and row["account_id"] is None
    assert audit is not None and audit["metadata"]["released_merchant"] == own


@pytest.mark.req("REQ-API-02")
async def test_reseed_keeps_the_admin_link(client: httpx.AsyncClient, db_pool: Pool) -> None:
    """QA-002: cada `docker compose run --rm api ...` roda o seed de novo; o vínculo fica."""
    from datetime import date

    from app.db.migrate import apply_seed

    await apply_seed(db_pool, date(2026, 11, 26))
    acc = await _merchant_account(db_pool, "seed@example.com")
    linked, _ = await repo.link_merchant(db_pool, merchant_user_id="cliente1988", account_id=acc.id)
    assert linked
    await apply_seed(db_pool, date(2026, 11, 27))
    assert await repo.merchant_user_id_for(db_pool, acc.id) == "cliente1988"
    async with db_pool.connection() as conn:
        n = await (
            await conn.execute(
                "SELECT count(*) AS n FROM transactions WHERE merchant_id = 'cliente1988'"
            )
        ).fetchone()
    assert n is not None and n["n"] > 0
