"""Profile registry — resolves profile names to ExecutionBackend instances.

Updated to use ProfileRepository (DB-backed) instead of an in-memory dict.
The module-level `registry` singleton is kept for backward compatibility
with api/backends.py and api/jobs.py — they import it the same way.

The registry seeds a 'default' Docker profile on first use if the
database is empty. This ensures the user can submit jobs immediately.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ..domain.backend_profile import BackendProfile
from ..repositories.db import SessionLocal
from ..repositories.profile_repository import ProfileRepository
from .backend_config import (
    BackendConfig,
    DockerBackendConfig,
    LocalBackendConfig,
    SlurmBackendConfig,
    create_backend,
)


class ProfileNotFoundError(Exception):
    def __init__(self, name: str):
        super().__init__(f"Backend profile '{name}' not found.")
        self.name = name


class ProfileAlreadyExistsError(Exception):
    def __init__(self, name: str):
        super().__init__(f"Backend profile '{name}' already exists.")
        self.name = name


class ProfileRegistry:
    """Facade over ProfileRepository that resolves names to backends.

    Opens its own short-lived DB sessions for each operation so it can
    be used both inside and outside FastAPI request context.
    """

    def _session(self) -> Session:
        return SessionLocal()

    def _ensure_default(self, repo: ProfileRepository) -> None:
        """Seed the default Docker profile if the DB is empty."""
        if repo.exists("default"):
            return

        default_config = DockerBackendConfig(
            cli="podman",
            image="cascade-openmc:latest",
            openmc_bin="/opt/miniconda/envs/openmc/bin/openmc",
            nuclear_data_path=str(Path.home() / ".cascade" / "data"),
            nuclear_data_container_path="/nuclear-data",
            jobs_base_dir=str(Path.home() / ".cascade" / "jobs"),
        )
        repo.save(BackendProfile(
            name="default",
            backend_type="docker",
            config_data=default_config.model_dump(),
            description="Default local Docker/Podman backend. Edit to match your setup.",
        ))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self) -> list[BackendProfile]:
        with self._session() as db:
            repo = ProfileRepository(db)
            self._ensure_default(repo)
            return repo.list()

    def get(self, name: str) -> BackendProfile:
        with self._session() as db:
            repo = ProfileRepository(db)
            self._ensure_default(repo)
            profile = repo.get(name)
            if profile is None:
                raise ProfileNotFoundError(name)
            return profile

    def create(self, profile: BackendProfile) -> BackendProfile:
        with self._session() as db:
            repo = ProfileRepository(db)
            if repo.exists(profile.name):
                raise ProfileAlreadyExistsError(profile.name)
            return repo.save(profile)

    def update(self, name: str, profile: BackendProfile) -> BackendProfile:
        with self._session() as db:
            repo = ProfileRepository(db)
            if not repo.exists(name):
                raise ProfileNotFoundError(name)
            return repo.update(name, profile)

    def delete(self, name: str) -> None:
        with self._session() as db:
            ProfileRepository(db).delete(name)

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def get_backend(self, profile_name: str):
        """Resolve a profile name to a ready ExecutionBackend instance."""
        profile = self.get(profile_name)
        config  = _deserialize_config(profile.backend_type, profile.config_data)
        return create_backend(config)

    def get_config(self, profile_name: str) -> BackendConfig:
        """Resolve a profile name to its typed BackendConfig."""
        profile = self.get(profile_name)
        return _deserialize_config(profile.backend_type, profile.config_data)


def _deserialize_config(
    backend_type: str,
    config_data: dict,
) -> DockerBackendConfig | LocalBackendConfig | SlurmBackendConfig:
    match backend_type:
        case "docker":
            return DockerBackendConfig(**config_data)
        case "local":
            return LocalBackendConfig(**config_data)
        case "slurm":
            return SlurmBackendConfig(**config_data)
        case _:
            raise ValueError(f"Unknown backend type '{backend_type}'.")


# Module-level singleton — same import path as before
registry = ProfileRegistry()