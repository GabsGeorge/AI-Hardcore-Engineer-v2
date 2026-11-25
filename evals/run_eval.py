"""Golden-set eval against a running API with the real model (REQ-OBS-06, REQ-ORC-01).

Exits 1 when the mean exact route accuracy < 95%. Tools, handoff, guardrails, pii and sources
expectations are reported. With `--runs N` the whole golden set is posted N times and the report
shows mean and minimum per metric plus the scenarios whose outcome changed between runs.

    docker compose --profile eval run --rm eval                       # 1 run
    docker compose --profile eval run --rm eval python evals/run_eval.py --runs 3 \
        --json /tmp/golden.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
TARGET_ROUTE_ACCURACY = 0.95

# The route check compares only the specialists the supervisor dispatched. Infrastructure nodes
# run on every turn and say nothing about routing, so they stay out of the comparison.
SPECIALISTS = frozenset({"knowledge", "support", "escalation"})
INFRA_NODES = frozenset(
    {"input_guardrail", "summarize", "supervisor", "synthesize", "output_guardrail"}
)
KNOWN_EXPECTATIONS = frozenset(
    {"route", "tools_contains", "handoff", "guardrails_input", "pii_masked", "has_sources"}
)


class UnknownExpectationError(ValueError):
    """The golden set declares something check() does not know how to score."""


def load_scenarios() -> list[dict[str, Any]]:
    data = yaml.safe_load((ROOT / "evals" / "golden_set.yaml").read_text(encoding="utf-8"))
    return list(data["scenarios"])


def specialists(route: list[str]) -> set[str]:
    return {node for node in route if node in SPECIALISTS}


def check(expected: dict[str, Any], body: dict[str, Any]) -> dict[str, bool]:
    """One bool per expectation the scenario declares. An expectation this function does not
    know raises UnknownExpectationError: nothing in the golden set is silently ignored.

    `route` is EXACT on specialists: the set of knowledge/support/escalation visited must equal
    the expected set (an extra or a missing specialist fails). `[safe_response]` means the
    input guardrail blocked the message."""
    unknown = set(expected) - KNOWN_EXPECTATIONS
    if unknown:
        raise UnknownExpectationError(
            f"expectativa desconhecida no golden set: {sorted(unknown)}; "
            f"conhecidas: {sorted(KNOWN_EXPECTATIONS)}"
        )
    blocked = body["guardrails"]["input"] == "blocked"
    out: dict[str, bool] = {}
    if "route" in expected:
        want = set(expected["route"])
        if want == {"safe_response"}:
            out["route"] = blocked
        else:
            invalid = want - SPECIALISTS
            if invalid:
                raise UnknownExpectationError(
                    f"rota esperada com nó que não é especialista: {sorted(invalid)}"
                )
            out["route"] = not blocked and specialists(body["route"]) == want
    if "tools_contains" in expected:
        used = {t["name"] for t in body["tools_used"]}
        out["tools"] = bool(used & set(expected["tools_contains"]))
    if "handoff" in expected:
        out["handoff"] = body["handoff"]["status"] == expected["handoff"]
    if "guardrails_input" in expected:
        out["guardrails"] = body["guardrails"]["input"] == expected["guardrails_input"]
    if "pii_masked" in expected:
        out["pii"] = (body["guardrails"]["input"] == "masked") == bool(expected["pii_masked"])
    if "has_sources" in expected:
        out["has_sources"] = bool(body.get("sources")) == bool(expected["has_sources"])
    return out


def _post(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/v1/chat", json=payload)
    if resp.status_code == 429:  # the golden set is close to the per-minute limit
        time.sleep(int(resp.headers.get("Retry-After", "60")))
        resp = client.post("/v1/chat", json=payload)
    resp.raise_for_status()
    return dict(resp.json())


def run_once(client: httpx.Client, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sc in scenarios:
        payload: dict[str, Any] = {"message": sc["message"]}
        if "context_message" in sc:
            payload["conversation_id"] = _post(client, {"message": sc["context_message"]})[
                "conversation_id"
            ]
        body = _post(client, payload)
        result = check(sc["expected"], body)
        rows.append({"id": sc["id"], "result": result, "route": body["route"]})
        status = "PASS" if all(result.values()) else "FAIL"
        print(f"{status}  {sc['id']}  {result}  {body['route']}")
    return rows


def summarize(runs: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """Per metric: hits/total per run, mean and minimum ratio; scenarios that flipped."""
    metrics: dict[str, list[float]] = {}
    counts: dict[str, list[str]] = {}
    for rows in runs:
        totals: dict[str, list[bool]] = {}
        for row in rows:
            for name, ok in row["result"].items():
                totals.setdefault(name, []).append(ok)
        for name, oks in totals.items():
            metrics.setdefault(name, []).append(sum(oks) / len(oks))
            counts.setdefault(name, []).append(f"{sum(oks)}/{len(oks)}")
    outcomes: dict[str, set[bool]] = {}
    for rows in runs:
        for row in rows:
            outcomes.setdefault(row["id"], set()).add(all(row["result"].values()))
    return {
        "runs": len(runs),
        "metrics": {
            name: {"per_run": counts[name], "mean": sum(v) / len(v), "min": min(v)}
            for name, v in metrics.items()
        },
        "unstable": sorted(sid for sid, seen in outcomes.items() if len(seen) > 1),
        "always_failing": sorted(sid for sid, seen in outcomes.items() if seen == {False}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--json", type=Path, default=None, help="grava runs + resumo em JSON")
    args = parser.parse_args(argv)

    from app.api.auth import create_token

    base_url = os.environ.get("EVAL_BASE_URL", "http://api:8000")
    token = create_token(os.environ.get("EVAL_USER", "cliente1988"), "merchant")
    scenarios = load_scenarios()
    runs: list[list[dict[str, Any]]] = []
    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=90
    ) as client:
        for index in range(args.runs):
            print(f"--- run {index + 1}/{args.runs}")
            runs.append(run_once(client, scenarios))
    summary = summarize(runs)
    for name, m in summary["metrics"].items():
        print(f"{name:<12} {' '.join(m['per_run'])}  mean={m['mean']:.1%} min={m['min']:.1%}")
    print(f"instáveis: {summary['unstable'] or 'nenhum'}")
    print(f"sempre falham: {summary['always_failing'] or 'nenhum'}")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"summary": summary, "runs": runs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    accuracy = summary["metrics"]["route"]["mean"]
    if accuracy < TARGET_ROUTE_ACCURACY:
        print(f"FAIL: route accuracy {accuracy:.0%} < {TARGET_ROUTE_ACCURACY:.0%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
