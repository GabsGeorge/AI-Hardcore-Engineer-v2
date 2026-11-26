"""Swagger, ReDoc e /openapi.json só fora de produção (QA-028)."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.main import create_app


@pytest.mark.req("REQ-SEC-05")
def test_docs_routes_are_off_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "production")
    app = create_app()
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not {"/docs", "/redoc", "/openapi.json"} & paths


@pytest.mark.req("REQ-SEC-05")
def test_docs_routes_stay_available_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "local")
    assert create_app().openapi_url == "/openapi.json"
