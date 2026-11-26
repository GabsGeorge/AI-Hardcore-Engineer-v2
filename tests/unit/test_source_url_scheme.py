"""Fontes da resposta: só URLs http/https chegam ao navegador (A3, LLM05)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.api.chat import build_response, is_http_url


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://site.getnet.com.br/maquininhas/", True),
        ("http://example.com/a?b=1", True),
        ("HTTPS://EXAMPLE.COM", True),
        ("javascript:alert(document.cookie)", False),
        ("JavaScript:alert(1)", False),
        ("  javascript:alert(1)", False),
        ("data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==", False),
        ("vbscript:msgbox(1)", False),
        ("/relativa/sem/host", False),
        ("https://", False),
        ("", False),
    ],
)
def test_is_http_url(url: str, ok: bool) -> None:
    assert is_http_url(url) is ok


def test_build_response_drops_non_http_sources() -> None:
    good = {"title": "Getnet", "url": "https://site.getnet.com.br/", "kind": "kb"}
    state = {
        "final_response": "ok",
        "route_trace": ["knowledge"],
        "guardrail_flags": ["input:pass", "output:pass"],
        "results": [
            {
                "agent": "knowledge",
                "answer": "ok",
                "confidence": 0.9,
                "error": None,
                "tools_used": [],
                "sources": [
                    good,
                    {"title": "x", "url": "javascript:alert(1)", "kind": "web"},
                    {"title": "y", "url": "data:text/html,<script>1</script>", "kind": "web"},
                ],
            }
        ],
    }
    body = build_response(state, uuid4(), "0" * 32)
    assert [s.url for s in body.sources] == [good["url"]]
