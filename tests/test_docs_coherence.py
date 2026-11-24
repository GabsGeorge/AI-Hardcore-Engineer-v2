"""Documentação ↔ código (D4): todo nome ``getnet_*`` e todo caminho ``deploy/*`` citado em
docs/reports/observability.md e no README precisa existir de verdade.

- ``getnet_*``: precisa ser uma família de métrica registrada no ``prometheus_client`` pela app
  (aceitando os sufixos ``_total``, ``_bucket``, ``_sum``, ``_count`` que o exporter gera) ou,
  se não for métrica, aparecer literalmente no código em ``src/``.
- ``deploy/*``: precisa existir no repositório (arquivo ou diretório; ``*`` vira glob).
- spans citados entre crases (``llm.*``, ``tool.*``, ``retrieval.*`` e os nós do grafo) precisam
  aparecer no código de instrumentação.

Sem Docker, sem rede: roda em qualquer máquina com as dependências do projeto.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "docs/reports/observability.md", ROOT / "README.md"]

METRIC_RE = re.compile(r"\bgetnet_[a-z0-9_]*[a-z0-9]")
# Pega também links relativos (`../../deploy/...`): o caractere antes de `deploy` pode ser `/`.
DEPLOY_RE = re.compile(r"(?<![\w.-])deploy/[A-Za-z0-9_.*/-]*[A-Za-z0-9_*/]")
SUFFIXES = ("_total", "_bucket", "_sum", "_count", "_created")


def _registered_metric_families() -> set[str]:
    # Importar a app registra todas as métricas (metrics.py e quem mais as defina).
    from prometheus_client import REGISTRY

    import app.main  # noqa: F401

    return {family.name for family in REGISTRY.collect()}


def _source_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))


def _cited(pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for doc in DOCS:
        for match in pattern.findall(doc.read_text(encoding="utf-8")):
            found.append((doc.relative_to(ROOT).as_posix(), match))
    return found


def _metric_exists(name: str, families: set[str], source: str) -> bool:
    candidates = {name} | {name[: -len(s)] for s in SUFFIXES if name.endswith(s)}
    if candidates & families:
        return True
    return re.search(rf"\b{re.escape(name)}\b", source) is not None


def test_docs_cite_at_least_the_core_metrics() -> None:
    """Guarda contra o teste passar vazio (regex quebrada ou doc apagado)."""
    names = {name for _, name in _cited(METRIC_RE)}
    assert {"getnet_http_requests_total", "getnet_online_eval_score"} <= names, names


def test_every_getnet_name_in_docs_exists_in_code() -> None:
    families = _registered_metric_families()
    source = _source_text()
    missing = sorted(
        {
            (doc, name)
            for doc, name in _cited(METRIC_RE)
            if not _metric_exists(name, families, source)
        }
    )
    assert not missing, f"nomes getnet_* citados que não existem no código: {missing}"


def test_every_deploy_path_in_docs_exists_in_repo() -> None:
    cited = _cited(DEPLOY_RE)
    assert cited, "nenhum caminho deploy/* citado — regex ou docs quebrados"
    missing = []
    for doc, raw in cited:
        path = raw.rstrip("/.")
        if "*" in path:
            if not list(ROOT.glob(path)):
                missing.append((doc, raw))
        elif not (ROOT / path).exists():
            missing.append((doc, raw))
    assert not missing, f"caminhos deploy/* citados que não existem: {sorted(set(missing))}"


SPAN_RE = re.compile(r"`((?:llm|tool|retrieval|supervisor)\.[a-z_<>]+)`")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_span_names_in_docs_exist_in_instrumentation(doc: Path) -> None:
    source = _source_text()
    for name in set(SPAN_RE.findall(doc.read_text(encoding="utf-8"))):
        if "<" in name:  # padrão documentado, ex.: llm.<papel> → prefixo f"llm.{...}"
            prefix = name.split("<", 1)[0]
            assert f'f"{prefix}{{' in source, f"{doc.name}: padrão de span {name} sem f-string"
            continue
        literal = f'"{name}"' in source
        prefixed = (
            name.split(".", 1)[0] in ("llm", "tool") and f'f"{name.split(".")[0]}.{{' in source
        )
        assert literal or prefixed, f"{doc.name}: span {name!r} não existe na instrumentação"
