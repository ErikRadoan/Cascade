Title: Choose Svelte for frontend, FastAPI for backend, uv for Python tool
Date: 2026-06-09
Status: Accepted

Context
-------
This is an academic research project with collaborators who may be less familiar with heavy JS stacks. We need a modern, lightweight frontend and a fast developer experience for the Python API.

Decision
--------
We will use Svelte for the frontend, FastAPI for the Python API, and uv as the Python package manager/runtime tool.

Consequences
------------
- Svelte provides small bundles and a gentle learning curve for collaborators.
- FastAPI gives async endpoints, great developer ergonomics, OpenAPI docs out of the box and works well with long-running background tasks.
- uv provides fast dependency resolution and lockfile support (uv.lock). We will commit uv.lock for reproducibility.

Notes
-----
This ADR documents why these choices were made. Implementation details (e.g., job-queue choices) will be recorded in subsequent ADRs.

