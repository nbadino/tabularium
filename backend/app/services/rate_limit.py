"""Rate limiting persistente per gli endpoint di autenticazione."""
from __future__ import annotations

import time

from ..adapters import rate_limits as store


def allow(key: str, *, limit: int = 10, window: float = 60.0) -> bool:
    """Count one attempt in the current window; False when the limit is reached.

    The counter owns its connection on purpose: it must survive the rollback of the
    request that failed, otherwise a rejected login would clear the evidence of the
    attempts that made it suspicious.
    """
    now = time.time()
    current = store.window(key)
    if current is None or now - current[0] >= window:
        store.start(key, now)
        return True
    if current[1] >= limit:
        return False
    store.bump(key)
    return True


def exhausted(key: str, *, limit: int, window: float = 60.0) -> bool:
    """True when `key` already reached `limit` in its window; counts nothing."""
    current = store.window(key)
    return current is not None and time.time() - current[0] < window and current[1] >= limit


def reset(key: str) -> None:
    store.clear(key)
