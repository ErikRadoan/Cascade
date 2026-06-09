# Cascade

Research project monorepo (frontend + backend + docs). This repo contains templates and infra for getting started.

Quick start (local)
1. Copy environment template:

   cp .env.example .env

2. Start services (requires Docker):

   docker-compose up --build

Development guidance
- Branching: use `dev` as integration branch and feature branches `feat/...` for work in progress.
- ADRs: add architecture decisions to `docs/decisions/`.

See `PROJECT_KICKOFF_CHECKLIST.md` for the full onboarding checklist.

