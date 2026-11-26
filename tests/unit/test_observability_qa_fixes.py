"""Observabilidade após o QA: juiz com o texto das fontes (QA-042), painéis sem "No data"
(QA-075..077) e latência do turno também no /v1/chat/stream (QA-078)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY

from app.api.chat import build_response, judge_sources
from app.observability.metrics import initialise_label_sets

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.req("REQ-OBS-05")
def test_judge_reads_the_passages_and_account_data() -> None:
    passage = "A antecipação pode ser feita no app Getnet, em crédito, débito ou Pix."
    terminal = '(account data from tool diagnose_terminal)\n{"status": "offline"}'
    state = {
        "results": [
            {
                "agent": "knowledge",
                "answer": "x",
                "confidence": 0.9,
                "error": None,
                "sources": [
                    {"title": "Antecipação", "url": "https://site.getnet.com.br/a/", "kind": "kb"}
                ],
                "tools_used": [],
                "evidence": [f"Antecipação — Como funciona\n{passage}"],
            },
            {
                "agent": "support",
                "answer": "y",
                "confidence": 1.0,
                "error": None,
                "sources": [],
                "tools_used": [],
                "evidence": [terminal],
            },
        ],
        "final_response": "ok",
        "guardrail_flags": [],
        "route_trace": [],
    }
    block = judge_sources(state, build_response(state, uuid4(), "a" * 32))
    assert passage in block and terminal in block
    assert "https://site.getnet.com.br/a/" in block


@pytest.mark.req("REQ-OBS-03")
def test_known_label_sets_exist_before_the_first_event() -> None:
    initialise_label_sets(["diagnose_terminal"])
    assert (
        REGISTRY.get_sample_value("getnet_tool_errors_total", {"tool": "diagnose_terminal"})
        is not None
    )
    assert (
        REGISTRY.get_sample_value("getnet_auth_login_total", {"outcome": "bad_password"})
        is not None
    )
    assert REGISTRY.get_sample_value("getnet_auth_email_sent_total", {"kind": "invite"}) is not None
    assert (
        REGISTRY.get_sample_value(
            "getnet_chat_turn_duration_seconds_count", {"endpoint": "/v1/chat/stream"}
        )
        is not None
    )


@pytest.mark.req("REQ-OBS-03")
def test_latency_panels_and_alert_cover_the_stream_route() -> None:
    alerts = (ROOT / "deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    assert "getnet_chat_turn_duration_seconds_bucket" in alerts
    for name in ("operational.json", "ui.json"):
        exprs = json.dumps(
            json.loads((ROOT / "deploy/grafana/dashboards" / name).read_text(encoding="utf-8"))
        )
        assert "getnet_chat_turn_duration_seconds_bucket" in exprs
        assert 'route=\\"/v1/chat\\"' not in exprs
