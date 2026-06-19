"""Cascade backend — FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.geometry import router as geometry_router
from .api.jobs import router as jobs_router
from .api.materials import router as materials_router
from .api.results import router as results_router
from .repositories.db import create_tables

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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/", tags=["system"])
async def root() -> dict:
    return {"name": "Cascade API", "docs": "/docs", "health": "/health"}