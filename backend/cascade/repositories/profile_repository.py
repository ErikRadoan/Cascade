"""Backend profile repository — persists named backend profiles to the database.

Replaces the in-memory dict in execution/profile_registry.py.
The ProfileRegistry class is updated to use this instead of self._profiles.

Usage:
    from ..repositories.profile_repository import ProfileRepository
    from ..repositories.db import get_db

    @router.get("/backends/profiles")
    def list_profiles(db: Session = Depends(get_db)):
        return ProfileRepository(db).list()
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..domain.backend_profile import BackendProfile
from .models import BackendProfileRow


class ProfileRepository:
    """CRUD operations for BackendProfile against the database."""

    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, profile: BackendProfile) -> BackendProfile:
        """Insert a new profile.

        Raises:
            ValueError: If a profile with this name already exists.
        """
        existing = self._db.get(BackendProfileRow, profile.name)
        if existing is not None:
            raise ValueError(
                f"Profile '{profile.name}' already exists. Use update() to modify it."
            )

        row = BackendProfileRow(
            name=         profile.name,
            backend_type= profile.backend_type,
            config_data=  profile.config_data,
            description=  profile.description,
            created_at=   profile.created_at,
            updated_at=   profile.updated_at,
        )
        self._db.add(row)
        self._db.commit()
        return self._row_to_domain(row)

    def update(self, name: str, profile: BackendProfile) -> BackendProfile:
        """Replace an existing profile's config and description.

        Preserves created_at. Updates updated_at to now.

        Raises:
            KeyError: If no profile with this name exists.
        """
        row = self._db.get(BackendProfileRow, name)
        if row is None:
            raise KeyError(f"Profile '{name}' not found.")

        row.backend_type = profile.backend_type
        row.config_data  = profile.config_data
        row.description  = profile.description
        row.updated_at   = datetime.now(timezone.utc)
        self._db.commit()
        return self._row_to_domain(row)

    def delete(self, name: str) -> None:
        """Delete a profile by name.

        Raises:
            KeyError:   If no profile with this name exists.
            ValueError: If trying to delete the 'default' profile.
        """
        if name == "default":
            raise ValueError(
                "The 'default' profile cannot be deleted. Edit it instead."
            )
        row = self._db.get(BackendProfileRow, name)
        if row is None:
            raise KeyError(f"Profile '{name}' not found.")
        self._db.delete(row)
        self._db.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, name: str) -> BackendProfile | None:
        """Get a profile by name, or None if not found."""
        row = self._db.get(BackendProfileRow, name)
        return self._row_to_domain(row) if row else None

    def list(self) -> list[BackendProfile]:
        """Return all profiles, alphabetically by name."""
        rows = (
            self._db.query(BackendProfileRow)
            .order_by(BackendProfileRow.name)
            .all()
        )
        return [self._row_to_domain(r) for r in rows]

    def exists(self, name: str) -> bool:
        return self._db.get(BackendProfileRow, name) is not None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _row_to_domain(self, row: BackendProfileRow) -> BackendProfile:
        return BackendProfile(
            name=         row.name,
            backend_type= row.backend_type,
            config_data=  row.config_data,
            description=  row.description,
            created_at=   row.created_at,
            updated_at=   row.updated_at,
        )