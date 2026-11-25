"""Modo local sem Docker (scripts/run_local.py) e o import preguiçoso do FlashRank."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_local

ROOT = Path(__file__).resolve().parent.parent.parent


def test_retriever_import_does_not_load_flashrank() -> None:
    """Sem o runtime MSVC o onnxruntime não carrega no Windows; com SKIP_RERANKER a api precisa
    subir sem importar o FlashRank."""
    code = "import sys, app.rag.retriever; print('flashrank' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False"


def test_local_env_controls_infra_and_disables_export_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SKIP_RERANKER", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    env = run_local.local_env(
        database_url="postgresql://x/getnet",
        redis_url="redis://127.0.0.1:6390/0",
        metrics_port=9100,
        otlp="",
        secrets_={"JWT_SECRET": "j" * 64},
    )
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == ""  # um .env com tempo:4318 não vaza
    assert env["SKIP_RERANKER"] == "1"
    assert env["JWT_SECRET"] == "j" * 64
    assert env["METRICS_PORT"] == "9100"


def test_local_secrets_are_created_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / ".local" / "secrets.json"
    first = run_local.local_secrets(path)
    assert set(first) == {"JWT_SECRET", "SESSION_SECRET", "PASSWORD_PEPPER"}
    assert all(len(v) == 64 for v in first.values())
    assert run_local.local_secrets(path) == first


def test_timezones_are_copied_only_when_missing(tmp_path: Path) -> None:
    pytest.importorskip("tzdata")  # só o grupo `local` garante o pacote
    assert run_local.ensure_timezones(tmp_path) is True
    assert (tmp_path / "timezone" / "America" / "Sao_Paulo").exists()
    assert run_local.ensure_timezones(tmp_path) is False
