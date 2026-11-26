"""LLM-as-judge offline sobre o golden set (REQ-OBS-04 — versão batch).

Reutiliza a maquinaria do juiz online (`src/app/ops/online_eval.py`) e aplica em cada
cenário do golden set, agregando faithfulness/relevance e emitindo matriz de confusão
rota-esperada × rota-real.

    docker compose --profile eval run --rm eval python evals/judge_batch.py

Saída: JSON em stdout + arquivo `/tmp/judge_report.json`. Cobra `faithfulness` e
`relevance` médios por tipo de cenário (knowledge, support, escalation, safe_response) e a nota
de cada cenário julgado.

O juiz lê a MESMA evidência que o juiz online (`judge_sources`: títulos/URLs mais as passagens e
os dados de conta de cada especialista), tirada do checkpoint da conversa. Antes lia só a lista
pública de fontes, sem texto: respostas de suporte (dados de conta, sem fonte) saíam com
faithfulness 0 e as de conhecimento eram julgadas contra URLs. A API pública não expõe tokens,
então o relatório não estima custo (antes somava um campo `usage` inexistente e dava 0).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_scenarios() -> list[dict[str, Any]]:
    data = yaml.safe_load((ROOT / "evals" / "golden_set.yaml").read_text(encoding="utf-8"))
    return list(data["scenarios"])


def _expected_route(expected: dict[str, Any]) -> str:
    route = expected.get("route", [])
    if route == ["safe_response"]:
        return "safe_response"
    if "escalation" in route:
        return "escalation"
    if "knowledge" in route:
        return "knowledge"
    if "support" in route:
        return "support"
    return "other"


def _observed_route(body: dict[str, Any]) -> str:
    route = body.get("route", [])
    if body.get("guardrails", {}).get("input") == "blocked":
        return "safe_response"
    if body.get("handoff", {}).get("status") == "awaiting_human":
        return "escalation"
    if "knowledge" in route:
        return "knowledge"
    if "support" in route:
        return "support"
    return "other"


async def _run() -> dict[str, Any]:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from app.api.auth import create_token
    from app.api.chat import graph_config, judge_sources
    from app.api.schemas import ChatResponse
    from app.core.config import get_settings
    from app.db.pool import open_pool
    from app.graph.builder import build_graph
    from app.ops.online_eval import _judge  # noqa: PLC2701 — reuso proposital

    base_url = os.environ.get("EVAL_BASE_URL", "http://api:8000")
    user_id = os.environ.get("EVAL_USER", "cliente1988")
    token = create_token(user_id, "merchant")
    graph = build_graph(AsyncPostgresSaver(await open_pool(get_settings().database_url)))
    judged: list[dict[str, Any]] = []

    cm: dict[tuple[str, str], int] = defaultdict(int)
    scores_by_bucket: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"faithfulness": [], "relevance": []}
    )
    totals: dict[str, int] = defaultdict(int)

    async with httpx.AsyncClient(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60
    ) as client:
        for sc in load_scenarios():
            payload: dict[str, Any] = {"message": sc["message"]}
            if "context_message" in sc:
                boot = await client.post("/v1/chat", json={"message": sc["context_message"]})
                payload["conversation_id"] = boot.json()["conversation_id"]
            resp = await client.post("/v1/chat", json=payload)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "60")))
                resp = await client.post("/v1/chat", json=payload)
            body = resp.json()

            exp = _expected_route(sc["expected"])
            obs = _observed_route(body)
            cm[(exp, obs)] += 1
            totals["requests"] += 1

            # LLM-as-judge: só avalia caminhos de resposta (não safe_response/escalation)
            if obs in {"knowledge", "support", "other"} and body.get("response"):
                response = ChatResponse.model_validate(body)
                config = graph_config(user_id, response.conversation_id, response.trace_id)
                state = (await graph.aget_state(config)).values
                verdict = await _judge(
                    sc["message"], response.response, judge_sources(state, response)
                )
                if verdict:
                    bucket = scores_by_bucket[exp]
                    bucket["faithfulness"].append(verdict.faithfulness)
                    bucket["relevance"].append(verdict.relevance)
                    judged.append(
                        {
                            "id": sc["id"],
                            "faithfulness": verdict.faithfulness,
                            "relevance": verdict.relevance,
                        }
                    )

    return {
        "confusion_matrix": {f"{e}->{o}": n for (e, o), n in sorted(cm.items())},
        "faithfulness_mean_by_bucket": {
            b: round(sum(v["faithfulness"]) / len(v["faithfulness"]), 3)
            for b, v in scores_by_bucket.items()
            if v["faithfulness"]
        },
        "relevance_mean_by_bucket": {
            b: round(sum(v["relevance"]) / len(v["relevance"]), 3)
            for b, v in scores_by_bucket.items()
            if v["relevance"]
        },
        "totals": {"requests": totals["requests"], "judged": len(judged)},
        "per_scenario": judged,
    }


def main() -> int:
    report = asyncio.run(_run())
    out = Path(tempfile.gettempdir()) / "judge_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
