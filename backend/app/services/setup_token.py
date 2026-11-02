"""The one-time token that guards the first-administrator setup.

Until the first user exists, `/api/auth/setup` would make whoever reaches the
instance first its administrator: on a public server that is anyone. The token is
written to `<data>/setup-token` (readable by the service user only) and to the
log at startup, like Jupyter's; the browser opened by `tabularium` carries it in
the URL. Whoever can read the server's log or data folder can claim the
instance, nobody else. It is deleted once the administrator exists.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

from .. import config

log = logging.getLogger("tabularium")


def path() -> Path:
    return config.ROOT_DIR / "setup-token"


def ensure() -> str:
    """The current token, created on first need; stable across restarts until used."""
    target = path()
    try:
        current = target.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        current = ""
    if current:
        return current
    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def matches(candidate: str | None) -> bool:
    try:
        expected = path().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    return bool(candidate and expected) and hmac.compare_digest(candidate.strip(), expected)


def clear() -> None:
    path().unlink(missing_ok=True)


def pending() -> str | None:
    """The token when the instance still waits for its administrator, else None."""
    if not config.auth_enabled():
        return None
    if not config.DB_PATH.exists():
        return ensure()  # do not create the database here: init_db owns its first write
    from ..db import connect
    from . import users as usersvc

    with connect() as conn:
        if usersvc.count_users(conn) > 0:
            clear()
            return None
    return ensure()


def announce() -> None:
    token = pending()
    if token is None:
        return
    public = os.environ.get("TABULARIUM_PUBLIC_URL", "").strip().rstrip("/")
    where = f"{public}/?setup_token={token}" if public else f"/?setup_token={token} on this instance"
    log.warning(
        "No administrator yet. Create it with the setup token %s (also in %s): open %s",
        token, path(), where,
    )
