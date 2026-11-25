"""Limite de cadastro e de pedido de recuperação de senha por IP e por e-mail (E2, REQ-SEC-08).

Sem limite, `/v1/auth/register` e `/v1/auth/password/reset-request` serviam para disparar
e-mails em massa para um endereço (bombardeio) ou varrer endereços a partir de um IP. Cada fluxo
tem duas contas em Redis por janela fixa de 1 h: uma por IP e uma por e-mail (o e-mail entra como
hash, nunca em claro). Passou de `AUTH_FLOW_LIMIT_PER_HOUR` em qualquer uma → 429 com
`Retry-After` até o fim da janela.

A contagem acontece ANTES de olhar se o e-mail existe e a resposta de 429 é a mesma para e-mail
novo e existente: o limite não revela cadastro (FR-004).

shortcut: janela fixa (pode permitir até 2x o limite na virada da hora), como o rate limit do chat.
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Literal

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.errors import AppError

AuthFlow = Literal["register", "reset_request"]
WINDOW_SECONDS = 3600


def _email_key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:32]


async def check_auth_flow_limit(request: Request, flow: AuthFlow, *, ip: str, email: str) -> None:
    redis: Redis = request.app.state.redis
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    keys = (
        f"authrl:{flow}:ip:{ip}:{window}",
        f"authrl:{flow}:email:{_email_key(email)}:{window}",
    )
    limit = get_settings().auth_flow_limit_per_hour
    over = False
    for key in keys:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, WINDOW_SECONDS + 60)
        over = over or count > limit
    if over:
        retry_after = max(1, math.ceil(WINDOW_SECONDS - now % WINDOW_SECONDS))
        raise AppError(
            429,
            "rate_limited",
            "Muitas tentativas. Tente novamente mais tarde.",
            headers={"Retry-After": str(retry_after)},
        )
