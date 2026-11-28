"""Golden set through the API with scripted models (REQ-ORC-01, T126/T127).

Every scenario of `evals/golden_set.yaml` runs here: the 10 of the challenge, the 8 extras and the
crediário ones. The scripts (tests/integration/scenarios.py) stand in for the model only; the
graph, guardrails, tools, retrieval and handoffs are real. Each answer is scored by the same
`check()` the real-model eval uses (`evals/run_eval.py`), so the CI and the eval agree on what a
scenario expects.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from typing import Any

import httpx
import pytest

from app.db.migrate import apply_seed
from app.db.pool import Pool
from tests.fakes import FakeLLM
from tests.fixtures.kb import add_document, fresh_retriever  # noqa: F401
from tests.integration import scenarios

_spec = importlib.util.spec_from_file_location("run_eval", scenarios.ROOT / "evals" / "run_eval.py")
assert _spec is not None and _spec.loader is not None
_run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_run_eval)
check = _run_eval.check

SCENARIOS = scenarios.load()


@pytest.mark.req("REQ-ORC-01", "REQ-OBS-06")
def test_golden_set_meets_minimum_coverage() -> None:
    assert len(SCENARIOS) >= 21
    ids = [s.id for s in SCENARIOS]
    assert len(set(ids)) == len(ids)
    # The 10 scenarios of the challenge and the 8 extras all have scripts.
    assert {f"sc-{n:02d}" for n in range(1, 19)} <= {i[:5] for i in ids}
    languages = {s.language for s in SCENARIOS}
    assert "pt" in languages and "en" in languages
    assert any("credi" in s.message.lower() for s in SCENARIOS), (
        "golden set deve cobrir crediário (produto BR relevante do catálogo)"
    )


async def _seed_passages(db_pool: Pool, scenario: scenarios.Scenario) -> None:
    turns = [t for t in (scenario.context_turn, scenario.turn) if t is not None]
    for n, (title, section, content) in enumerate(p for t in turns for p in t.kb):
        url = f"https://site.getnet.com.br/ajuda/{scenario.id}-{n}/"
        await add_document(db_pool, url, title, scenario.language, [(section, content)])


@pytest.mark.req("REQ-ORC-01", "REQ-API-02")
@pytest.mark.usefixtures("fresh_retriever")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
async def test_golden_scenario_through_the_api(
    client: httpx.AsyncClient,
    db_pool: Pool,
    fake_llm: FakeLLM,
    auth_headers: dict[str, dict[str, str]],
    scenario: scenarios.Scenario,
) -> None:
    await apply_seed(db_pool, date(2026, 10, 13))
    await _seed_passages(db_pool, scenario)
    headers = auth_headers["cliente1988"]
    payload: dict[str, Any] = {"message": scenario.message}

    if scenario.context_turn is not None:
        fake_llm.script(**scenario.context_turn.scripts)
        first = await client.post(
            "/v1/chat", json={"message": scenario.context_message}, headers=headers
        )
        assert first.status_code == 200, first.text
        payload["conversation_id"] = first.json()["conversation_id"]

    fake_llm.script(**scenario.turn.scripts)
    resp = await client.post("/v1/chat", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    result = check(scenario.expected, body)
    assert result and all(result.values()), (
        f"{scenario.id}: {result} | route={body['route']} tools="
        f"{[t['name'] for t in body['tools_used']]} sources={len(body['sources'])} "
        f"guardrails={body['guardrails']} handoff={body['handoff']}"
    )
    if scenario.expected.get("pii_masked"):
        assert "123.456.789-00" not in body["response"]
