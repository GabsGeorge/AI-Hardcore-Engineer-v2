"""Api para o E2E do navegador: a mesma app, com o LLM roteirizado de `tests/e2e_llm.py` e duas
contas de teste já ativas (lojista vinculado ao `cliente1988` e operador).

Só roda pelo `docker-compose.test.yml` (o arquivo vive em `tests/`, fora da imagem de produção) e
se recusa a subir fora de APP_ENV=test. As senhas abaixo são valores de teste, usados também pelo
spec `apps/web/e2e/07-hitl-realtime.spec.ts`.
"""

from __future__ import annotations

import asyncio
import os
import sys

E2E_PASSWORD = "e2e-senha-de-teste-123456"
E2E_ACCOUNTS = {
    "merchant": ("e2e-lojista@getnet.example.com", "Lojista E2E"),
    "operator": ("e2e-operador@getnet.example.com", "Operador E2E"),
}


async def seed_accounts() -> None:
    from app.auth import passwords, repo
    from app.core.config import get_settings
    from app.db.migrate import migrate
    from app.db.pool import close_pool, open_pool

    pool = await open_pool(get_settings().database_url)
    try:
        await migrate(pool)
        for role, (email, name) in E2E_ACCOUNTS.items():
            account = await repo.get_account_by_email(pool, email)
            if account is None:
                account = await repo.create_account(
                    pool, email=email, display_name=name, role=role, status="active"
                )
            await repo.set_password(pool, account.id, passwords.hash_password(E2E_PASSWORD))
            await repo.activate_account(pool, account.id)
            if role == "merchant":
                await repo.link_merchant(
                    pool, merchant_user_id="cliente1988", account_id=account.id
                )
    finally:
        await close_pool()


def main() -> None:
    if os.environ.get("APP_ENV") != "test":
        sys.exit("tests.e2e_server só roda com APP_ENV=test")
    from tests.e2e_llm import install

    install()
    asyncio.run(seed_accounts())
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, access_log=False)  # noqa: S104


if __name__ == "__main__":
    main()
