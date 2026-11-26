"""Contrato de ``POST /v1/feedback`` (REQ-OBS-05)."""

from __future__ import annotations

import httpx
import pytest

from app.db import repo
from app.db.pool import Pool

_TRACE = "0123456789abcdef0123456789abcdef"
_OTHER_TRACE = "fedcba9876543210fedcba9876543210"


async def _answered(db_pool: Pool, user_id: str, trace_id: str) -> None:
    """A turn the user received: only those can be rated (QA-024)."""
    conversation_id = await repo.get_or_create_conversation(db_pool, user_id, None)
    await repo.record_turn(
        db_pool,
        trace_id=trace_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=None,
        sources=[],
    )


@pytest.fixture(autouse=True)
async def _own_turn(db_pool: Pool) -> None:
    await _answered(db_pool, "cliente1988", _TRACE)


@pytest.mark.req("REQ-OBS-05")
async def test_feedback_links_to_trace_and_persists(
    client: httpx.AsyncClient,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": _TRACE, "rating": 1, "comment": "muito bom"},
        headers=auth_headers["cliente1988"],
    )
    assert resp.status_code == 202
    async with db_pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT user_id, rating, comment, trace_id FROM feedback WHERE trace_id = %s",
                (_TRACE,),
            )
        ).fetchone()
    assert row is not None
    assert row["user_id"] == "cliente1988"
    assert row["rating"] == 1
    assert row["trace_id"] == _TRACE


@pytest.mark.req("REQ-OBS-05")
async def test_feedback_masks_pii_in_comment(
    client: httpx.AsyncClient,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": _TRACE, "rating": -1, "comment": "fala no fulano.silva@example.com"},
        headers=auth_headers["cliente1988"],
    )
    assert resp.status_code == 202
    async with db_pool.connection() as conn:
        row = await (
            await conn.execute("SELECT comment FROM feedback WHERE trace_id = %s", (_TRACE,))
        ).fetchone()
    assert row is not None
    assert "fulano.silva@example.com" not in row["comment"], "PII bruta não pode persistir"


@pytest.mark.req("REQ-OBS-05")
async def test_feedback_comment_limit_enforced(
    client: httpx.AsyncClient, auth_headers: dict[str, dict[str, str]]
) -> None:
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": _TRACE, "rating": 1, "comment": "x" * 1001},
        headers=auth_headers["cliente1988"],
    )
    assert resp.status_code == 422


@pytest.mark.req("REQ-OBS-05")
async def test_feedback_rating_must_be_plus_or_minus_one(
    client: httpx.AsyncClient, auth_headers: dict[str, dict[str, str]]
) -> None:
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": _TRACE, "rating": 0},
        headers=auth_headers["cliente1988"],
    )
    assert resp.status_code == 422


@pytest.mark.req("REQ-OBS-05", "REQ-SEC-03")
async def test_feedback_on_another_merchants_trace_is_not_found(
    client: httpx.AsyncClient,
    db_pool: Pool,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    """QA-024: o trace de uma resposta do cliente2024 não pode ser avaliado pelo cliente1988."""
    await _answered(db_pool, "cliente2024", _OTHER_TRACE)
    resp = await client.post(
        "/v1/feedback",
        json={"trace_id": _OTHER_TRACE, "rating": -1},
        headers=auth_headers["cliente1988"],
    )
    assert resp.status_code == 404
    unknown = await client.post(
        "/v1/feedback",
        json={"trace_id": "a" * 32, "rating": 1},
        headers=auth_headers["cliente1988"],
    )
    assert unknown.status_code == 404
    async with db_pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) AS n FROM feedback WHERE trace_id = %s", (_OTHER_TRACE,)
            )
        ).fetchone()
    assert row is not None and row["n"] == 0
