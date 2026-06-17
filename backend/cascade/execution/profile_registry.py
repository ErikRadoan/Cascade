"""Profile registry — stores and retrieves backend profiles.

In-memory implementation for now. Replace with a repository backed by
SQLite/Postgres when the database layer is added.

The registry is the single source of truth for profiles. Both the
backends API router and the jobs router import from here.

Pre-seeded with a default Docker profile so the user can submit jobs
immediately without creating a profile first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cascade.domain.backend_profile import BackendProfile
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
    """Stores named backend profiles and resolves them to ExecutionBackend instances.

    Usage:
        registry = ProfileRegistry()
        registry.create(profile)
        backend = registry.get_backend("my_cluster")
    """

    def __init__(self):
        self._profiles: dict[str, BackendProfile] = {}
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        """Pre-seed with a default Docker profile.

        The user can submit jobs immediately without creating a profile.
        They can edit this profile or create new ones via the API.
        """
        default_docker = DockerBackendConfig(
            cli="podman",
            image="cascade-openmc:latest",
            openmc_bin="/opt/miniconda/envs/openmc/bin/openmc",
            nuclear_data_path=str(Path.home() / ".cascade" / "data"),
            nuclear_data_container_path="/nuclear-data",
            jobs_base_dir=str(Path.home() / ".cascade" / "jobs"),
        )
        self._profiles["default"] = BackendProfile(
            name="default",
            backend_type="docker",
            config_data=default_docker.model_dump(),
            description="Default local Docker/Podman backend. Edit to match your setup.",
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self) -> list[BackendProfile]:
        """Return all profiles, alphabetically by name."""
        return sorted(self._profiles.values(), key=lambda p: p.name)

    def get(self, name: str) -> BackendProfile:
        """Get a profile by name.

        Raises:
            ProfileNotFoundError: If no profile with that name exists.
        """
        profile = self._profiles.get(name)
        if profile is None:
            raise ProfileNotFoundError(name)
        return profile

    def create(self, profile: BackendProfile) -> BackendProfile:
        """Save a new profile.

        Raises:
            ProfileAlreadyExistsError: If a profile with that name already exists.
        """
        if profile.name in self._profiles:
            raise ProfileAlreadyExistsError(profile.name)
        self._profiles[profile.name] = profile
        return profile

    def update(self, name: str, profile: BackendProfile) -> BackendProfile:
        """Replace an existing profile.

        The profile's name field must match the name argument.

        Raises:
            ProfileNotFoundError: If no profile with that name exists.
        """
        if name not in self._profiles:
            raise ProfileNotFoundError(name)
        updated = BackendProfile(
            name=profile.name,
            backend_type=profile.backend_type,
            config_data=profile.config_data,
            description=profile.description,
            created_at=self._profiles[name].created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._profiles[name] = updated
        return updated

    def delete(self, name: str) -> None:
        """Delete a profile by name.

        Raises:
            ProfileNotFoundError: If no profile with that name exists.
            ValueError: If trying to delete the 'default' profile.
        """
        if name not in self._profiles:
            raise ProfileNotFoundError(name)
        if name == "default":
            raise ValueError(
                "The 'default' profile cannot be deleted. Edit it instead."
            )
        del self._profiles[name]

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def get_backend(self, profile_name: str):
        """Resolve a profile name to a ready-to-use ExecutionBackend instance.

        Args:
            profile_name: Name of a saved profile.

        Returns:
            An ExecutionBackend subclass instance.

        Raises:
            ProfileNotFoundError: If no profile with that name exists.
            ValueError: If the profile's backend_type is unrecognised.
        """
        profile = self.get(profile_name)
        config  = _deserialize_config(profile.backend_type, profile.config_data)
        return create_backend(config)

    def get_config(self, profile_name: str) -> BackendConfig:
        """Resolve a profile name to its typed BackendConfig.

        Used by the jobs router to store the config dict alongside each job
        so the correct backend can be reconstructed for status/cancel calls.

        Raises:
            ProfileNotFoundError: If no profile with that name exists.
        """
        profile = self.get(profile_name)
        return _deserialize_config(profile.backend_type, profile.config_data)


def _deserialize_config(
    backend_type: str,
    config_data: dict,
) -> DockerBackendConfig | LocalBackendConfig | SlurmBackendConfig:
    """Deserialize a raw config dict to the correct typed BackendConfig.

    Args:
        backend_type: "docker", "local", or "slurm"
        config_data:  Raw dict from BackendProfile.config_data

    Returns:
        Typed, validated BackendConfig subclass instance.

    Raises:
        ValueError: If backend_type is unrecognised.
    """
    match backend_type:
        case "docker":
            return DockerBackendConfig(**config_data)
        case "local":
            return LocalBackendConfig(**config_data)
        case "slurm":
            return SlurmBackendConfig(**config_data)
        case _:
            raise ValueError(
                f"Unknown backend type '{backend_type}'. "
                f"Must be one of: docker, local, slurm."
            )


# ---------------------------------------------------------------------------
# Module-level singleton — imported by both routers
# ---------------------------------------------------------------------------
# Both api/backends.py and api/jobs.py import this instance.
# When the DB is added, replace with a repository-backed implementation.

registry = ProfileRegistry()