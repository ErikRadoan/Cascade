"""Cascade backend — FastAPI application entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from cascade.api.backends import router as backends_router
from cascade.api.geometry import router as geometry_router
from cascade.api.jobs import router as jobs_router
from cascade.api.materials import router as materials_router
from cascade.api.projects import router as projects_router
from cascade.api.results import router as results_router
from cascade.repositories.db import create_tables

app = FastAPI(
    title="Cascade",
    description=(
        "UX framework for parametric nuclear Monte Carlo simulations. "
        "Supports OpenMC and Serpent 2 via a YAML geometry DSL with "
        "built-in parametric sweep orchestration."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Startup — create tables on first run
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    """Create database tables if they don't exist.

    Safe to run on every startup — uses CREATE TABLE IF NOT EXISTS.
    Replace with Alembic migrations before going to production.
    """
    create_tables()


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(geometry_router,  prefix="/api")
app.include_router(materials_router, prefix="/api")
app.include_router(jobs_router,      prefix="/api")
app.include_router(results_router,   prefix="/api")
app.include_router(backends_router,  prefix="/api")
app.include_router(projects_router, prefix="/api")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Optional: serve the built frontend (standalone mode)
# ---------------------------------------------------------------------------
# In dev the frontend runs separately (`vite dev` on :5173) and nothing here
# changes. In the standalone app the SvelteKit static build (frontend/build/)
# is shipped next to / inside the backend and served from the same origin, so
# no CORS and no second process are needed.
#
# Lookup order for the static directory:
#   1. $CASCADE_STATIC_DIR                      (explicit override)
#   2. <PyInstaller bundle>/static              (frozen app; see scripts/build_standalone.py)
#   3. <repo>/frontend/build                    (running from source after `npm run build`)
# If none exists, "/" returns the small JSON info document as before.
#
# MUST stay registered AFTER every API router and /health: the catch-all below
# would otherwise shadow them.

def _find_static_dir() -> Path | None:
    candidates: list[Path] = []
    if os.getenv("CASCADE_STATIC_DIR"):
        candidates.append(Path(os.environ["CASCADE_STATIC_DIR"]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "static")
    candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "build")
    return next((c for c in candidates if (c / "index.html").is_file()), None)


_STATIC_DIR = _find_static_dir()

if _STATIC_DIR is not None:
    _STATIC_ROOT = _STATIC_DIR.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str) -> FileResponse:
        # Unknown API paths must stay real 404s (JSON clients), not index.html.
        if path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404)
        # Resolve + containment check: never serve outside the static dir (../ etc).
        target = (_STATIC_ROOT / path).resolve()
        if path and target.is_file() and _STATIC_ROOT in target.parents:
            return FileResponse(target)
        return FileResponse(_STATIC_ROOT / "index.html")  # SPA fallback
else:
    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {"name": "Cascade API", "docs": "/docs", "health": "/health"}
