"""Taxas do guardrail de entrada com o classificador REAL (E1), contra uma api de pé.

Posta cada ataque de tests/security/data/attacks.yaml e cada frase legítima de
tests/security/data/legit_phrases.yaml em /v1/chat e conta `guardrails.input == "blocked"`.
Gasta tokens (um turno por frase); a heurística sozinha é medida sem LLM em
tests/unit/test_input_guardrail_false_positives.py.

    python evals/guard_rates.py --base-url http://localhost:8000 --token "$TOKEN" \
        --json /tmp/guard_rates.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

DATA = Path(__file__).resolve().parent.parent / "tests" / "security" / "data"


def _post(client: httpx.Client, message: str) -> dict[str, Any]:
    for _ in range(3):
        resp = client.post("/v1/chat", json={"message": message})
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", "60")))
            continue
        resp.raise_for_status()
        return dict(resp.json())
    raise RuntimeError("rate limit persistente")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    attacks = yaml.safe_load((DATA / "attacks.yaml").read_text(encoding="utf-8"))
    legit = yaml.safe_load((DATA / "legit_phrases.yaml").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {args.token}"}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=90) as client:
        for kind, items in (
            ("attack", [(a["id"], a["message"]) for a in attacks]),
            ("legit", [(f"legit-{i}", m) for i, m in enumerate(legit, 1)]),
        ):
            for item_id, message in items:
                body = _post(client, message)
                blocked = body["guardrails"]["input"] == "blocked"
                rows.append(
                    {"kind": kind, "id": item_id, "blocked": blocked, "route": body["route"]}
                )
                print(f"{kind:<6} {'BLOCK' if blocked else 'pass '} {item_id}")
    attack_rows = [r for r in rows if r["kind"] == "attack"]
    legit_rows = [r for r in rows if r["kind"] == "legit"]
    summary = {
        "attack_blocked": f"{sum(r['blocked'] for r in attack_rows)}/{len(attack_rows)}",
        "legit_false_positive": f"{sum(r['blocked'] for r in legit_rows)}/{len(legit_rows)}",
        "missed_attacks": [r["id"] for r in attack_rows if not r["blocked"]],
        "blocked_legit": [r["id"] for r in legit_rows if r["blocked"]],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.json is not None:
        args.json.write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
