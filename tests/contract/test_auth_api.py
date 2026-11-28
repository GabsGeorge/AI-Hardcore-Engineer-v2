"""Contract of ``/v1/auth/*`` against ``specs/002-web-ui/contracts/auth.yaml`` (T036).

Every response the API gives on these paths is validated by Schemathesis against the contract:
the status code must be one the contract documents for that operation, and the body must match
its schema. The happy path and the documented error paths of each operation are exercised with
real requests through the app; the deep generative sweep is out of the fast suite.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosmtplib
import httpx
import pytest
import schemathesis
from pydantic import SecretStr
from schemathesis.specs.openapi.checks import ignored_auth

from app.auth import one_time_tokens, passwords, repo
from app.core.config import get_settings
from app.db.pool import Pool

pytestmark = pytest.mark.req("REQ-API-02")

CONTRACT = Path(__file__).resolve().parents[2] / "specs" / "002-web-ui" / "contracts" / "auth.yaml"
SCHEMA = schemathesis.openapi.from_path(str(CONTRACT))
EMAIL = "contrato@loja.example"
PASSWORD = f"pw-{secrets.token_urlsafe(18)}"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "session_secret", SecretStr("x" * 64))
    monkeypatch.setattr(s, "password_pepper", SecretStr("pepper-" * 4))


async def _no_smtp(*args: object, **kwargs: object) -> None:
    return None


def _check(
    method: str,
    path: str,
    response: httpx.Response,
    body: Any = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Status documented for the operation and body valid against its schema. For an
    authenticated call, pass the client: the case then carries the same session cookie and CSRF
    header the request had, so Schemathesis does not read it as an unauthenticated call."""
    operation = SCHEMA[path][method.upper()]
    kwargs: dict[str, Any] = {} if body is None else {"body": body}
    if client is not None:
        kwargs["cookies"] = {"session": client.cookies["session"]}
        kwargs["headers"] = {"X-CSRF-Token": client.cookies["csrf"]}
    # ignored_auth would re-send the call without credentials over the network (no server at
    # http://test here); unauthenticated access is covered in tests/security/.
    operation.Case(**kwargs).validate_response(response, excluded_checks=[ignored_auth])


async def _account(db_pool: Pool, *, status: str = "active") -> repo.AccountRow:
    account = await repo.create_account(
        db_pool, email=EMAIL, display_name="Loja Contrato", role="merchant", status=status
    )
    await repo.set_password(db_pool, account.id, passwords.hash_password(PASSWORD))
    return account


async def _login(client: httpx.AsyncClient) -> httpx.Response:
    body = {"email": EMAIL, "password": PASSWORD}
    response = await client.post("/v1/auth/login", json=body)
    _check("post", "/v1/auth/login", response, body)
    assert response.status_code == 200
    return response


def test_every_contract_operation_exists_in_the_app(app: Any) -> None:
    served = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if path.startswith("/v1/auth/")
    }
    for operation in SCHEMA.get_all_operations():
        op = operation.ok()
        assert (op.method.upper(), op.path) in served, f"{op.method} {op.path} not served"


async def test_register_new_and_existing_email_are_generic(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    await _account(db_pool)
    for email in ("nova@loja.example", EMAIL):
        body = {"display_name": "Loja Nova", "email": email, "password": PASSWORD}
        with patch.object(aiosmtplib, "send", side_effect=_no_smtp):
            response = await client.post("/v1/auth/register", json=body)
        _check("post", "/v1/auth/register", response, body)
        assert response.status_code == 202


async def test_register_weak_password_is_422(client: httpx.AsyncClient, db_pool: Pool) -> None:
    body = {"display_name": "Loja Fraca", "email": "fraca@loja.example", "password": "123"}
    response = await client.post("/v1/auth/register", json=body)
    _check("post", "/v1/auth/register", response, body)
    assert response.status_code == 422


async def test_verify_email_valid_then_used_token(client: httpx.AsyncClient, db_pool: Pool) -> None:
    account = await _account(db_pool, status="pending")
    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="verify_email")
    for expected in (200, 410):
        response = await client.post("/v1/auth/verify-email", json={"token": token})
        _check("post", "/v1/auth/verify-email", response, {"token": token})
        assert response.status_code == expected


async def test_login_ok_and_bad_password(client: httpx.AsyncClient, db_pool: Pool) -> None:
    await _account(db_pool)
    await _login(client)
    # A browser that is not logged in: with the session cookie and no X-CSRF-Token the CSRF
    # middleware answers 403 first (tests/security/test_csrf_middleware.py).
    client.cookies.clear()
    body = {"email": EMAIL, "password": "errada-" + secrets.token_hex(6)}
    response = await client.post("/v1/auth/login", json=body)
    _check("post", "/v1/auth/login", response, body)
    assert response.status_code == 401


async def test_sessions_list_revoke_others_and_logout(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    await _account(db_pool)
    await _login(client)
    csrf = {"X-CSRF-Token": client.cookies["csrf"]}

    listed = await client.get("/v1/auth/sessions")
    _check("get", "/v1/auth/sessions", listed, client=client)
    assert listed.status_code == 200 and len(listed.json()) == 1

    revoked = await client.delete("/v1/auth/sessions", headers=csrf)
    _check("delete", "/v1/auth/sessions", revoked, client=client)
    assert revoked.status_code == 204

    session = {"session": client.cookies["session"], "csrf": client.cookies["csrf"]}
    out = await client.post("/v1/auth/logout", headers=csrf)
    operation = SCHEMA["/v1/auth/logout"]["POST"]
    operation.Case(cookies={"session": session["session"]}, headers=csrf).validate_response(
        out, excluded_checks=[ignored_auth]
    )
    assert out.status_code == 204


async def test_reset_request_then_reset_then_reused_link(
    client: httpx.AsyncClient, db_pool: Pool
) -> None:
    account = await _account(db_pool)
    with patch.object(aiosmtplib, "send", side_effect=_no_smtp):
        asked = await client.post("/v1/auth/password/reset-request", json={"email": EMAIL})
    _check("post", "/v1/auth/password/reset-request", asked, {"email": EMAIL})
    assert asked.status_code == 202

    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="password_reset")
    body = {"token": token, "new_password": f"nova-{secrets.token_urlsafe(16)}"}
    for expected in (200, 410):
        response = await client.post("/v1/auth/password/reset", json=body)
        _check("post", "/v1/auth/password/reset", response, body)
        assert response.status_code == expected


async def test_reset_with_a_weak_password_is_422(client: httpx.AsyncClient, db_pool: Pool) -> None:
    account = await _account(db_pool)
    token = await one_time_tokens.mint(db_pool, account_id=account.id, purpose="password_reset")
    body = {"token": token, "new_password": "curta"}  # below the 12-character minimum
    response = await client.post("/v1/auth/password/reset", json=body)
    _check("post", "/v1/auth/password/reset", response, body)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/auth/login", {}),
        ("/v1/auth/login", {"email": "not-an-email", "password": 3}),
        ("/v1/auth/register", {"email": "x@loja.example"}),
        ("/v1/auth/verify-email", {}),
        ("/v1/auth/password/reset-request", {"email": None}),
        ("/v1/auth/password/reset", {"token": "t"}),
    ],
)
async def test_malformed_bodies_get_documented_errors(
    client: httpx.AsyncClient, db_pool: Pool, path: str, body: dict[str, Any]
) -> None:
    """Malformed input never yields a 5xx and every error is one the contract documents."""
    response = await client.post(path, json=body)
    assert response.status_code < 500, response.text
    _check("post", path, response, body)
