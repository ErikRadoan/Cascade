"""FastAPI app entrypoint for Cascade."""
from __future__ import annotations
from fastapi import FastAPI
from .api.geometry import router as geometry_router
from .api.jobs import router as jobs_router
from .api.materials import router as materials_router
from .api.results import router as results_router
from .config import CascadeSettings


def create_app() -> FastAPI:
    app = FastAPI(title="Cascade")
    app.include_router(geometry_router, prefix="/api")
    app.include_router(materials_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(results_router, prefix="/api")

    @app.get("/")
    def root() -> dict[str, object]:
        return {"message": "Cascade backend scaffold", "status": "ok"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
