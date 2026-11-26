"""Heurística do input_guardrail (E1): frases legítimas de lojista passam; ataques continuam
bloqueados.

A taxa de falso positivo é medida sobre LEGIT (precisa ser 0) e a de bloqueio só da heurística
sobre tests/security/data/attacks.yaml; os números são publicados em
docs/reports/final-review.md. A taxa com o classificador LLM fica em
tests/security/test_injection_direct.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.guardrails.input import _heuristic_hit

LEGIT: list[str] = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "security" / "data" / "legit_phrases.yaml").read_text(
        encoding="utf-8"
    )
)

ATTACKS = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "security" / "data" / "attacks.yaml").read_text(
        encoding="utf-8"
    )
)


def test_there_are_at_least_30_legit_phrases() -> None:
    assert len(LEGIT) >= 30
    assert len(set(LEGIT)) == len(LEGIT)


@pytest.mark.req("REQ-GR-01")
@pytest.mark.parametrize("message", LEGIT)
def test_legit_merchant_phrase_passes_the_heuristic(message: str) -> None:
    assert not _heuristic_hit(message), f"falso positivo da heurística: {message!r}"


def heuristic_rates() -> tuple[float, float]:
    """(falso positivo em LEGIT, bloqueio da heurística em attacks.yaml)."""
    false_positive = sum(_heuristic_hit(m) for m in LEGIT) / len(LEGIT)
    blocked = sum(_heuristic_hit(a["message"]) for a in ATTACKS) / len(ATTACKS)
    return false_positive, blocked


@pytest.mark.req("REQ-GR-01")
def test_heuristic_rates() -> None:
    false_positive, blocked = heuristic_rates()
    print(f"heurística: falso positivo {false_positive:.1%}, bloqueio {blocked:.1%}")
    assert false_positive == 0.0
    # Sem as regex de CPF/CNPJ, as 6 tentativas de IDOR por documento (e outras sem gatilho
    # textual) dependem do classificador LLM; a heurística sozinha não chega a 95% e não
    # precisa: test_injection_direct.py mede o bloqueio com o classificador.
    assert blocked >= 0.84
