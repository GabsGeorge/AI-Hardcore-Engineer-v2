"""Export de spans só quando OTEL_EXPORTER_OTLP_ENDPOINT está definido (REQ-OBS-02).

Roda `configure_tracing` num processo limpo (o provider global do OTel só pode ser definido uma
vez) e confere quais span processors foram registrados.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

_PROBE = """
import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
from opentelemetry import trace
from app.observability.tracing import configure_tracing, get_tracer
configure_tracing(sys.argv[1])
with get_tracer().start_as_current_span("probe"):
    pass
provider = trace.get_tracer_provider()
processors = provider._active_span_processor._span_processors
print(",".join(type(p).__name__ for p in processors) or "none")
provider.shutdown()
"""


def _probe(endpoint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _PROBE, endpoint],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )


@pytest.mark.req("REQ-OBS-02")
def test_empty_endpoint_registers_no_exporter_and_logs_nothing() -> None:
    out = _probe("")
    assert out.stdout.strip() == "none"
    assert "export" not in out.stderr.lower(), out.stderr


@pytest.mark.req("REQ-OBS-02")
def test_endpoint_registers_batch_exporter() -> None:
    # Porta fechada: o shutdown tenta exportar e falha, o que só prova que o exporter existe.
    out = _probe("http://127.0.0.1:9")
    assert out.stdout.strip() == "BatchSpanProcessor"
