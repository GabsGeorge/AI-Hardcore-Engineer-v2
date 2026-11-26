"""Per-user rate limit and Idempotency-Key replay in Redis (REQ-SEC-08, REQ-API-06).

The rate limit is a sliding 60 s window (a sorted set of request times per user): a fixed
per-minute counter let a burst that crossed the minute boundary through, up to twice the limit
(QA-027).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any
from uuid import uuid4

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.errors import AppError

_IDEM_TTL = 24 * 3600
# Longer than any turn, short enough that a crashed request doesn't lock its key for a day.
_IN_FLIGHT_TTL = 120


_WINDOW_SECONDS = 60


def _now() -> float:
    return time.time()


async def check_rate_limit(request: Request, user_id: str) -> None:
    """At most ``rate_limit_per_min`` accepted requests in any 60 s window. A refused request
    is not counted, and Retry-After is when the oldest accepted one leaves the window."""
    redis: Redis = request.app.state.redis
    now = _now()
    key = f"rl:{user_id}"
    member = f"{now:.6f}:{uuid4().hex[:8]}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, now - _WINDOW_SECONDS)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.zrange(key, 0, 0, withscores=True)
        pipe.expire(key, _WINDOW_SECONDS + 1)
        _, _, count, oldest, _ = await pipe.execute()
    if count > get_settings().rate_limit_per_min:
        await redis.zrem(key, member)
        oldest_at = float(oldest[0][1]) if oldest else now
        retry_after = max(1, math.ceil(oldest_at + _WINDOW_SECONDS - now))
        raise AppError(
            429,
            "rate_limited",
            f"Too many requests. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def _hash_body(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def idempotency_get_or_claim(
    request: Request, user_id: str, key: str | None, body: dict[str, Any]
) -> dict[str, Any] | None:
    """Cached response on replay; ``None`` means "run it and call idempotency_store"."""
    if key is None:
        return None
    redis: Redis = request.app.state.redis
    rec_key = f"idem:{user_id}:{key}"
    body_hash = _hash_body(body)
    claim = json.dumps({"body_hash": body_hash, "response": None})
    if await redis.set(rec_key, claim, ex=_IN_FLIGHT_TTL, nx=True):
        return None
    raw = await redis.get(rec_key)
    if raw is None:  # the claim expired between our two calls
        return None
    rec = json.loads(raw)
    if rec["body_hash"] != body_hash:
        raise AppError(
            409, "idempotency_conflict", "Idempotency-Key reused with a different payload."
        )
    if rec["response"] is None:
        raise AppError(
            409, "idempotency_in_progress", "A request with this Idempotency-Key is running."
        )
    return dict(rec["response"])


async def idempotency_store(
    request: Request, user_id: str, key: str | None, body: dict[str, Any], response: dict[str, Any]
) -> None:
    if key is None:
        return
    redis: Redis = request.app.state.redis
    rec = {"body_hash": _hash_body(body), "response": response}
    await redis.set(f"idem:{user_id}:{key}", json.dumps(rec), ex=_IDEM_TTL)
