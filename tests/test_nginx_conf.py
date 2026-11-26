"""deploy/nginx/default.conf: cabeçalhos de segurança em toda resposta e API resolvida por
requisição (QA-001, QA-029..QA-035)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("REQ-INF-01")

ROOT = Path(__file__).resolve().parent.parent
CONF = (ROOT / "deploy/nginx/default.conf").read_text(encoding="utf-8")
SECURITY_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Content-Security-Policy",
)


def _locations(conf: str) -> list[str]:
    """Body of each `location { ... }` block (no nested blocks in this file)."""
    return re.findall(r"\blocation\b[^{]*\{([^}]*)\}", conf)


def test_no_location_overrides_the_server_security_headers() -> None:
    # Um add_header dentro de location descarta TODOS os add_header do server.
    bodies = _locations(CONF)
    assert bodies, "nenhum bloco location encontrado"
    assert all("add_header" not in body for body in bodies)
    server_level = re.split(r"^\s*location\s", CONF, maxsplit=1, flags=re.M)[0]
    for header in SECURITY_HEADERS:
        assert f"add_header {header}" in server_level


def test_api_is_resolved_per_request() -> None:
    # Upstream estático guarda o IP do api da partida; recriar o api derrubava a UI com 502.
    assert not re.search(r"^\s*upstream\s+\S+\s*\{", CONF, flags=re.M)
    assert "include /tmp/nginx-resolver.conf;" in CONF
    proxied = re.findall(r"proxy_pass\s+([^;]+);", CONF)
    assert proxied and all(target.strip() == "$api" for target in proxied)


def test_resolver_script_is_installed_in_the_image() -> None:
    dockerfile = (ROOT / "apps/web/Dockerfile").read_text(encoding="utf-8")
    script = (ROOT / "apps/web/nginx-resolver.sh").read_text(encoding="utf-8")
    assert "/docker-entrypoint.d/" in dockerfile and "nginx-resolver.sh" in dockerfile
    assert "nginx-resolver.conf" in script and "/etc/resolv.conf" in script
