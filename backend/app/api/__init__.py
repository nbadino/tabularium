"""API routes package.

`register_routers(app)` raccoglie tutti i router; aggiungere qui i nuovi
moduli man mano che le milestone li introducono.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import (
    auth,
    blocks,
    cloud,
    datasets,
    evaluate,
    models,
    pages,
    playground,
    prelabel,
    projects,
    recognition,
    settings,
    system,
    training,
    users,
)


def register_routers(app: FastAPI) -> None:
    # Pubblici: health/info e l'intero gate di autenticazione (status/setup/login).
    app.include_router(system.router)
    app.include_router(auth.router)
    # Riservati: il resto delle API, protette da get_current_user in ogni router.
    app.include_router(settings.router)
    app.include_router(users.router)
    app.include_router(projects.router)
    app.include_router(pages.router)
    app.include_router(blocks.router)
    app.include_router(datasets.router)
    app.include_router(models.router)
    app.include_router(training.router)
    app.include_router(evaluate.router)
    app.include_router(playground.router)
    app.include_router(prelabel.router)
    app.include_router(recognition.router)
    app.include_router(cloud.router)
