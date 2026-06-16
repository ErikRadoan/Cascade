# Cascade
Research project monorepo for a FastAPI backend, a Svelte frontend, and supporting docs.
## Layout
- `backend/cascade/`: FastAPI app, domain models, services, adapters, execution backends, and repositories
- `frontend/`: SvelteKit-style frontend scaffold
- `docs/`: architecture notes and decision records
## Quick start
1. Copy environment template:
   `cp .env.example .env`
2. Start services (requires Docker):
   `docker-compose up --build`
## Development notes
- The shared Python project configuration currently lives at the repo root in `pyproject.toml`.
- Add architecture decisions to `docs/decisions/` and higher-level architecture notes to `docs/architecture/`.

