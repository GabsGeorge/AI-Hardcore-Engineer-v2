"""run_eval (B1, B2): rota EXATA nos especialistas e nenhuma expectativa ignorada."""

from __future__ import annotations

from typing import Any

import pytest

from evals.run_eval import UnknownExpectationError, check, load_scenarios, summarize


def _body(
    route: list[str],
    guard: str = "pass",
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "route": route,
        "guardrails": {"input": guard},
        "handoff": {"status": "none"},
        "tools_used": [],
        "sources": sources or [],
    }


INFRA = ["input_guardrail", "supervisor", "synthesize", "output_guardrail"]


@pytest.mark.req("REQ-OBS-06")
def test_exact_route_passes() -> None:
    body = _body([*INFRA, "knowledge", "support"])
    assert check({"route": ["knowledge", "support"]}, body)["route"] is True


@pytest.mark.req("REQ-OBS-06")
def test_extra_specialist_fails() -> None:
    body = _body(["input_guardrail", "supervisor", "knowledge", "escalation", "synthesize"])
    assert check({"route": ["knowledge"]}, body)["route"] is False


@pytest.mark.req("REQ-OBS-06")
def test_missing_specialist_fails() -> None:
    assert check({"route": ["knowledge", "support"]}, _body([*INFRA, "support"]))["route"] is False


@pytest.mark.req("REQ-OBS-06")
def test_infrastructure_nodes_are_ignored() -> None:
    body = _body(["input_guardrail", "summarize", "supervisor", "support", "supervisor"])
    assert check({"route": ["support"]}, body)["route"] is True


@pytest.mark.req("REQ-OBS-06")
def test_safe_response_still_means_blocked() -> None:
    assert check({"route": ["safe_response"]}, _body([], guard="blocked"))["route"] is True
    assert check({"route": ["safe_response"]}, _body(["knowledge"]))["route"] is False


@pytest.mark.req("REQ-OBS-06")
def test_has_sources_is_checked() -> None:
    src = [{"title": "Getnet", "url": "https://site.getnet.com.br/", "kind": "kb"}]
    assert check({"has_sources": True}, _body(["knowledge"], sources=src))["has_sources"] is True
    assert check({"has_sources": True}, _body(["knowledge"]))["has_sources"] is False


@pytest.mark.req("REQ-OBS-06")
def test_unknown_expectation_fails_loudly() -> None:
    with pytest.raises(UnknownExpectationError, match="expected_tone"):
        check({"route": ["knowledge"], "expected_tone": "polite"}, _body(["knowledge"]))


@pytest.mark.req("REQ-OBS-06")
def test_route_with_a_non_specialist_node_is_rejected() -> None:
    with pytest.raises(UnknownExpectationError, match="synthesize"):
        check({"route": ["synthesize"]}, _body(["synthesize"]))


@pytest.mark.req("REQ-OBS-06")
def test_golden_set_only_declares_known_expectations() -> None:
    for sc in load_scenarios():
        check(sc["expected"], _body([]))  # levanta se houver chave desconhecida


@pytest.mark.req("REQ-OBS-06")
def test_summary_reports_mean_min_and_unstable() -> None:
    ok = {"id": "a", "result": {"route": True}, "route": []}
    bad = {"id": "a", "result": {"route": False}, "route": []}
    always = {"id": "b", "result": {"route": False}, "route": []}
    summary = summarize([[ok, always], [bad, always]])
    assert summary["metrics"]["route"] == {"per_run": ["1/2", "0/2"], "mean": 0.25, "min": 0.0}
    assert summary["unstable"] == ["a"]
    assert summary["always_failing"] == ["b"]
