# Contributing

Branching strategy
- main: stable, review-ready history (protected when remote is created)
- dev: integration branch for the next release
- feature branches: feat/<short-description> (e.g. feat/geometry-viewport)

Workflow
- Create feature branches off `dev`.
- Open PR to `dev` for review. When `dev` is ready, open PR to `main`.

Commits
- Use meaningful, imperative commit messages. Optionally follow Conventional Commits.

ADRs
- Add architectural decisions as files under `docs/decisions/` with an incremental numeric prefix.

Local development
- Use the provided `docker-compose.yml` for a one-command local dev environment.

