"""Roda a api sem Docker: Postgres + pgvector embutido, Redis em processo e uvicorn.

    uv sync --group local
    uv run python scripts/run_local.py                 # api em http://127.0.0.1:8000
    uv run python scripts/run_local.py --otlp http://127.0.0.1:4318   # spans para o Jaeger

O que o script faz, nesta ordem:

1. Sobe um Postgres 16 com pgvector a partir do pacote ``pgserver`` (binários embutidos), com os
   dados em ``.local/pgdata``. No Windows o pacote vem sem banco de fusos horários; o script copia
   o do pacote ``tzdata`` (o seed usa ``America/Sao_Paulo``).
2. Sobe um Redis em processo (``fakeredis``) em 127.0.0.1:6390. Para usar um Redis real, passe
   ``--redis-url``.
3. Gera segredos locais (JWT, sessão, pepper) em ``.local/secrets.json`` na primeira execução e
   os reutiliza depois, para que tokens e sessões sobrevivam a um restart.
4. Roda o job de ingest (migrações + seed de demonstração; sem ``OPENAI_API_KEY`` a ingestão de
   páginas é pulada, como no compose).
5. Sobe a api com uvicorn num ``SelectorEventLoop``: o psycopg assíncrono não funciona com o
   ``ProactorEventLoop`` padrão do Windows.

Variáveis do ``.env`` continuam valendo (``OPENAI_API_KEY``, modelos...), exceto as que o modo
local precisa controlar: ``DATABASE_URL``, ``REDIS_URL``, ``METRICS_PORT``, ``SKIP_RERANKER`` e
``OTEL_EXPORTER_OTLP_ENDPOINT`` (vazio, a não ser com ``--otlp``).

Não é modo de produção: ``fakeredis`` não persiste nada e o Postgres embutido é para uma pessoa.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import selectors
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_NAME = "getnet"


def _selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def run_async(main: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """asyncio.run com o loop que o psycopg aceita em qualquer sistema."""
    if sys.platform == "win32":
        return asyncio.run(main(), loop_factory=_selector_loop)
    return asyncio.run(main())


def ensure_timezones(pg_share: Path) -> bool:
    """Copia o zoneinfo do pacote `tzdata` quando o Postgres embutido veio sem `timezone/`.
    Devolve True quando copiou."""
    target = pg_share / "timezone"
    if target.exists():
        return False
    import tzdata

    shutil.copytree(Path(tzdata.__file__).parent / "zoneinfo", target)
    return True


def _get_server(pgserver: Any, data_dir: Path, wait_seconds: float = 180.0) -> Any:
    """`cleanup_mode="stop"`: o Postgres para junto com o script (Ctrl-C incluso), sem deixar
    processo órfão. Depois de um encerramento forçado, a recuperação do WAL pode passar do tempo
    de espera do `pg_ctl` (TimeoutExpired) mesmo com o servidor subindo; nesse caso tentamos de
    novo até ele aceitar conexões, porque o pgserver reaproveita um servidor já de pé."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return pgserver.get_server(data_dir, cleanup_mode="stop")
        except subprocess.TimeoutExpired:
            if time.monotonic() > deadline:
                raise
            print("run_local: Postgres ainda em recuperação; aguardando...", flush=True)
            time.sleep(3)


def start_postgres(data_dir: Path) -> str:
    """Sobe (ou reaproveita) o Postgres embutido e devolve a URL do banco da app."""
    try:
        import pgserver
        import psycopg
    except ImportError as exc:  # pragma: no cover - mensagem para quem esqueceu o grupo
        raise SystemExit("Instale o grupo local: uv sync --group local") from exc

    pg_share = Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql"
    if pg_share.exists() and ensure_timezones(pg_share):
        print("run_local: banco de fusos horários copiado do pacote tzdata", flush=True)

    data_dir.mkdir(parents=True, exist_ok=True)
    server = _get_server(pgserver, data_dir)
    admin_url = server.get_uri()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone():
            conn.execute(f"CREATE DATABASE {DB_NAME}")
    app_url = admin_url.rsplit("/", 1)[0] + f"/{DB_NAME}"
    with psycopg.connect(app_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return app_url


def start_fake_redis(port: int) -> str:
    try:
        from fakeredis import TcpFakeServer
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Instale o grupo local: uv sync --group local") from exc

    server = TcpFakeServer(("127.0.0.1", port), server_type="redis")
    threading.Thread(target=server.serve_forever, daemon=True, name="fakeredis").start()
    return f"redis://127.0.0.1:{port}/0"


def local_secrets(path: Path) -> dict[str, str]:
    """Segredos estáveis entre execuções, criados uma vez (nunca versionados: `.local/`)."""
    if path.exists():
        data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        return data
    data = {
        "JWT_SECRET": secrets.token_hex(32),
        "SESSION_SECRET": secrets.token_hex(32),
        "PASSWORD_PEPPER": secrets.token_hex(32),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def local_env(
    *, database_url: str, redis_url: str, metrics_port: int, otlp: str, secrets_: dict[str, str]
) -> dict[str, str]:
    env = {
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "METRICS_PORT": str(metrics_port),
        "OTEL_EXPORTER_OTLP_ENDPOINT": otlp,
        # FlashRank precisa do onnxruntime (no Windows, do runtime MSVC); o padrão local é a
        # estratégia densa sem rerank, como no compose.
        "SKIP_RERANKER": os.environ.get("SKIP_RERANKER", "1"),
    }
    for key, value in secrets_.items():
        env[key] = os.environ.get(key) or value
    return env


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--metrics-port", type=int, default=9100)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local")
    parser.add_argument("--redis-url", default="", help="Redis real; vazio = fakeredis em processo")
    parser.add_argument("--redis-port", type=int, default=6390)
    parser.add_argument(
        "--otlp", default="", help="endpoint OTLP/HTTP, ex.: http://127.0.0.1:4318 (Jaeger)"
    )
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args(argv)

    database_url = start_postgres(args.data_dir / "pgdata")
    redis_url = args.redis_url or start_fake_redis(args.redis_port)
    os.environ.update(
        local_env(
            database_url=database_url,
            redis_url=redis_url,
            metrics_port=args.metrics_port,
            otlp=args.otlp,
            secrets_=local_secrets(args.data_dir / "secrets.json"),
        )
    )
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    os.chdir(ROOT)  # prompts/ e .env são resolvidos a partir da raiz

    if not args.skip_ingest:
        from app.rag import ingest

        run_async(ingest.main)

    import uvicorn

    from app.api.auth import create_token

    print(
        "run_local: token de demonstração (cliente1988, merchant):\n"
        f"  {create_token('cliente1988', 'merchant')}\n"
        f"run_local: api em http://{args.host}:{args.port}  "
        f"métricas em http://127.0.0.1:{args.metrics_port}/metrics"
        + (f"  spans → {args.otlp}" if args.otlp else ""),
        flush=True,
    )
    config = uvicorn.Config("app.main:app", host=args.host, port=args.port)
    run_async(uvicorn.Server(config).serve)


if __name__ == "__main__":
    main()
