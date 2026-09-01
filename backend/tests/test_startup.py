"""Regressioni per i controlli eseguiti durante il lifecycle del backend."""
from __future__ import annotations

import asyncio

import pytest

from app import main
from app.services import backup as backup_service


def test_startup_stops_on_corrupt_database(monkeypatch):
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(
        backup_service,
        "integrity",
        lambda: {"ok": False, "messages": ["database disk image is malformed"]},
    )

    async def enter_lifespan():
        async with main.lifespan(main.app):
            raise AssertionError("il lifecycle non dovrebbe entrare nell'app")

    with pytest.raises(RuntimeError, match="database non integro all'avvio"):
        asyncio.run(enter_lifespan())
