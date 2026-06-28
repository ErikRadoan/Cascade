"""Backend profile routes — CRUD for named execution profiles.

Profiles are named, saved backend configurations. The user creates them
once and picks them by name when submitting jobs. The 'default' profile
(Docker/Podman) is seeded automatically on first use.

Mount this router on the jobs router or the main app:
    from .api.backend_profiles import router as profiles_router
    app.include_router(profiles_router, prefix="/api/jobs/backends/profiles")
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..repositories.db import get_db
from ..repositories.profile_repository import ProfileRepository
from ..domain.backend_profile import BackendProfile
from ..execution.profile_registry import (
    ProfileRegistry,
    ProfileNotFoundError,
    ProfileAlreadyExistsError,
    registry,
)

router = APIRouter(prefix="/jobs/backends/profiles", tags=["backend-profiles"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ProfileCreateRequest(BaseModel):
    """Body for POST /backends/profiles."""
    name:         str  = Field(..., min_length=1, max_length=64,
                               pattern=r'^[a-zA-Z0-9_\-]+$',
                               description="Unique profile name. Alphanumeric, dashes, underscores.")
    backend_type: str  = Field(..., description="One of: docker, local, slurm")
    config_data:  dict = Field(..., description="Backend-specific config fields.")
    description:  str | None = Field(None, description="Optional human-readable note.")


class ProfileUpdateRequest(BaseModel):
    """Body for PUT /backends/profiles/{name}."""
    backend_type: str  = Field(..., description="One of: docker, local, slurm")
    config_data:  dict = Field(..., description="Backend-specific config fields.")
    description:  str | None = Field(None)


class ProfileResponse(BaseModel):
    """Wire format for a backend profile."""
    name:         str
    backend_type: str
    config_data:  dict
    description:  str | None
    created_at:   str
    updated_at:   str

    @classmethod
    def from_domain(cls, p: BackendProfile) -> "ProfileResponse":
        return cls(
            name=         p.name,
            backend_type= p.backend_type,
            config_data=  p.config_data,
            description=  p.description,
            created_at=   p.created_at.isoformat(),
            updated_at=   p.updated_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_BACKEND_TYPES = {"docker", "local", "slurm"}

def _validate_config(backend_type: str, config_data: dict) -> dict:
    """Validate config_data against the appropriate Pydantic model.

    Returns the model's .model_dump() (with defaults filled in).
    Raises HTTPException 422 on validation failure.
    """
    from ..execution.backend_config import (
        DockerBackendConfig, LocalBackendConfig, SlurmBackendConfig,
    )
    from pydantic import ValidationError

    _cls_map = {
        "docker": DockerBackendConfig,
        "local":  LocalBackendConfig,
        "slurm":  SlurmBackendConfig,
    }

    if backend_type not in _VALID_BACKEND_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown backend type '{backend_type}'. Must be one of: {', '.join(_VALID_BACKEND_TYPES)}.",
        )

    cls = _cls_map[backend_type]
    try:
        validated = cls(**config_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())

    return validated.model_dump()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[ProfileResponse])
async def list_profiles() -> list[ProfileResponse]:
    """List all backend profiles, alphabetically."""
    profiles = registry.list()
    return [ProfileResponse.from_domain(p) for p in profiles]


@router.get("/{name}", response_model=ProfileResponse)
async def get_profile(name: str) -> ProfileResponse:
    """Get a single backend profile by name."""
    try:
        profile = registry.get(name)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ProfileResponse.from_domain(profile)


@router.post("/", response_model=ProfileResponse, status_code=201)
async def create_profile(body: ProfileCreateRequest) -> ProfileResponse:
    """Create a new named backend profile.

    Config is validated against the backend type's Pydantic model before saving.
    The 'default' name is reserved — edit it via PUT instead.
    """
    if body.name == "default":
        raise HTTPException(
            status_code=409,
            detail="The 'default' profile cannot be created — it is seeded automatically. Edit it via PUT.",
        )

    validated_config = _validate_config(body.backend_type, body.config_data)

    profile = BackendProfile(
        name=         body.name,
        backend_type= body.backend_type,
        config_data=  validated_config,
        description=  body.description,
    )

    try:
        saved = registry.create(profile)
    except ProfileAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return ProfileResponse.from_domain(saved)


@router.put("/{name}", response_model=ProfileResponse)
async def update_profile(name: str, body: ProfileUpdateRequest) -> ProfileResponse:
    """Replace a profile's config and description.

    created_at is preserved. updated_at is bumped.
    """
    validated_config = _validate_config(body.backend_type, body.config_data)

    profile = BackendProfile(
        name=         name,
        backend_type= body.backend_type,
        config_data=  validated_config,
        description=  body.description,
    )

    try:
        updated = registry.update(name, profile)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ProfileResponse.from_domain(updated)


@router.delete("/{name}", status_code=204)
async def delete_profile(name: str) -> None:
    """Delete a profile.

    The 'default' profile cannot be deleted — edit it instead.
    Returns 204 No Content on success.
    """
    try:
        registry.delete(name)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))