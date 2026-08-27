"""API routes package.

`register_routers(app)` raccoglie tutti i router; aggiungere qui i nuovi
moduli man mano che le milestone li introducono.
"""
from __future__ import annotations

from fastapi import FastAPI

from . import blocks, datasets, evaluate, pages, playground, prelabel, projects, system, training


def register_routers(app: FastAPI) -> None:
    app.include_router(system.router)
    app.include_router(projects.router)
    app.include_router(pages.router)
    app.include_router(blocks.router)
    app.include_router(datasets.router)
    app.include_router(training.router)
    app.include_router(evaluate.router)
    app.include_router(playground.router)
    app.include_router(prelabel.router)