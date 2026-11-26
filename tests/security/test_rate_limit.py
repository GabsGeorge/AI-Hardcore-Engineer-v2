"""Rate limit por janela deslizante de 60 s (REQ-SEC-08).

A 31ª requisição em qualquer janela de 60 s deve retornar 429 com Retry-After, inclusive numa
rajada que atravessa a virada do minuto (QA-027).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.api import limits
from app.core.errors import AppError
from tests.fakes import FakeLLM, make_plan
from tests.fixtures.kb import fresh_retriever, rewrite  # noqa: F401


@pytest.mark.req("REQ-SEC-08")
@pytest.mark.usefixtures("fresh_retriever")
async def test_rate_limit_429(
    client: httpx.AsyncClient,
    fake_llm: FakeLLM,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    """A 31ª chamada do mesmo lojista no minuto corrente leva 429 + Retry-After."""
    fake_llm.script(
        supervisor=[make_plan("knowledge") for _ in range(40)],
        knowledge_rewrite=[rewrite() for _ in range(40)],
    )
    headers = auth_headers["cliente2024"]  # user "limpo" para não interferir em outros testes
    # 30 permitidas
    for i in range(30):
        resp = await client.post("/v1/chat", json={"message": f"q{i}"}, headers=headers)
        assert resp.status_code == 200, f"iteração {i} caiu para {resp.status_code}"
    # 31ª bloqueia
    blocked = await client.post("/v1/chat", json={"message": "q31"}, headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after")
    assert blocked.json()["error"]["code"] == "rate_limited"


@pytest.mark.req("REQ-SEC-08")
async def test_burst_across_the_minute_boundary_is_limited(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 pedidos meio segundo antes da virada do minuto e mais 10 depois: o 31º é recusado.
    Com o contador por minuto fixo, os 40 passavam."""
    clock = {"now": 1_800_000_059.5}
    monkeypatch.setattr(limits, "_now", lambda: clock["now"])
    request: Any = SimpleNamespace(app=app)
    user = f"burst-{uuid4().hex}"
    for _ in range(20):
        await limits.check_rate_limit(request, user)
    clock["now"] += 1.0
    for _ in range(10):
        await limits.check_rate_limit(request, user)
    with pytest.raises(AppError) as refused:
        await limits.check_rate_limit(request, user)
    assert refused.value.status == 429
    assert refused.value.headers is not None
    assert int(refused.value.headers["Retry-After"]) == 59
    # Quando os 20 primeiros saem da janela, volta a aceitar.
    clock["now"] += 59.1
    await limits.check_rate_limit(request, user)
