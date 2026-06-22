"""Material repository — persists the material library to disk.

Storage: ~/.cascade/materials.json (one JSON file per installation).
Format: a JSON object mapping material_id -> material record.

This is intentionally file-backed rather than SQLite-backed because:
- The library is imported/exported as JSON by users and external tools
- It needs to be human-readable and directly editable if needed
- A flat file is easier to back up and share than extracting from SQLite
- Read performance at 400 materials is instantaneous from disk

Thread safety: FastAPI is single-process for typical usage. The file is
read on every request and written on every mutation — no caching. For
larger deployments, add a mutex around writes.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.material import Material

# ---------------------------------------------------------------------------
# Pre-seeded defaults — written on first initialisation if the file
# does not exist. These cover a basic PWR pin cell so the user can
# run simulations immediately without importing any library.
# ---------------------------------------------------------------------------

_CASCADE_DIR = Path.home() / ".cascade"
_LIBRARY_PATH = _CASCADE_DIR / "materials.json"

_BUILTIN_MATERIALS = [
    {
        "id": "UO2", "name": "Uranium Dioxide",
        "density": 10.97, "library_tag": "builtin",
        "composition": {"U235": 0.03072, "U238": 0.96928, "O16": 2.0},
    },
    {
        "id": "He", "name": "Helium Gap",
        "density": 0.0001786, "library_tag": "builtin",
        "composition": {"He4": 1.0},
    },
    {
        "id": "Zr4", "name": "Zircaloy-4",
        "density": 6.56, "library_tag": "builtin",
        "composition": {
            "Zr90": 0.5145, "Zr91": 0.1122, "Zr92": 0.1715,
            "Zr94": 0.1738, "Zr96": 0.0280,
        },
    },
    {
        "id": "H2O", "name": "Light Water",
        "density": 0.7, "library_tag": "builtin",
        "composition": {"H1": 2.0, "O16": 1.0},
    },
    {
        "id": "B4C", "name": "Boron Carbide",
        "density": 2.52, "library_tag": "builtin",
        "composition": {"B10": 0.144, "B11": 0.576, "C12": 0.28},
    },
    {
        "id": "SS316", "name": "Stainless Steel 316",
        "density": 8.0, "library_tag": "builtin",
        "composition": {
            "Fe56": 0.6395, "Cr52": 0.170, "Ni58": 0.120,
            "Mo98": 0.025, "Mn55": 0.020, "Si28": 0.010,
        },
    },
    {
        "id": "FLiBe", "name": "FLiBe Molten Salt (2LiF-BeF2)",
        "density": 1.94, "library_tag": "builtin",
        "composition": {"Li6": 0.076, "Li7": 0.924, "Be9": 0.5, "F19": 2.5},
    },
    {
        "id": "Graphite", "name": "Nuclear Grade Graphite",
        "density": 1.70, "library_tag": "builtin",
        "composition": {"C12": 1.0},
    },
]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class MaterialRepository:
    """CRUD + search operations for the material library.

    Usage:
        repo = MaterialRepository()
        mats = repo.search("uranium")
        repo.save(material)
    """

    def __init__(self, path: Path = _LIBRARY_PATH):
        self._path = path
        self._ensure_initialised()

    # ------------------------------------------------------------------
    # Private I/O
    # ------------------------------------------------------------------

    def _ensure_initialised(self) -> None:
        """Create the library file with built-in materials if absent."""
        _CASCADE_DIR.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            data = {m["id"]: m for m in _BUILTIN_MATERIALS}
            self._write(data)

    def _read(self) -> dict[str, dict]:
        """Read the full library from disk."""
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        """Write the full library to disk atomically."""
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, material_id: str) -> Material | None:
        data = self._read()
        record = data.get(material_id)
        return self._from_record(record) if record else None

    def list_all(self) -> list[Material]:
        """Return all materials sorted by id."""
        data = self._read()
        return sorted(
            (self._from_record(r) for r in data.values()),
            key=lambda m: m.id,
        )

    def search(
        self,
        query: str = "",
        library_tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Material], int]:
        """Search materials by id/name/nuclide, optionally filtered by library.

        Args:
            query:       Free-text search against id, name, and nuclide names.
                         Case-insensitive. Empty string returns all.
            library_tag: If provided, only return materials from this library.
            limit:       Max results to return.
            offset:      Pagination offset.

        Returns:
            (matching_materials, total_count_before_pagination)
        """
        data = self._read()
        q = query.strip().lower()

        results: list[Material] = []
        for record in data.values():
            if library_tag and record.get("library_tag") != library_tag:
                continue

            if q:
                searchable = (
                    record.get("id", "").lower()
                    + " " + record.get("name", "").lower()
                    + " " + " ".join(record.get("composition", {}).keys()).lower()
                )
                if q not in searchable:
                    continue

            results.append(self._from_record(record))

        results.sort(key=lambda m: m.id)
        total = len(results)
        return results[offset : offset + limit], total

    def list_libraries(self) -> list[str]:
        """Return all distinct library_tag values in the library."""
        data = self._read()
        tags: set[str] = set()
        for record in data.values():
            tag = record.get("library_tag")
            if tag:
                tags.add(tag)
        return sorted(tags)

    def exists(self, material_id: str) -> bool:
        return material_id in self._read()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, material: Material, library_tag: str = "user") -> Material:
        """Insert a new material. Raises ValueError if ID already exists."""
        data = self._read()
        if material.id in data:
            raise ValueError(
                f"Material '{material.id}' already exists. "
                f"Use update() to modify it."
            )
        data[material.id] = self._to_record(material, library_tag)
        self._write(data)
        return material

    def update(self, material: Material, library_tag: str | None = None) -> Material:
        """Update an existing material. Raises KeyError if not found."""
        data = self._read()
        if material.id not in data:
            raise KeyError(f"Material '{material.id}' not found.")
        existing_tag = data[material.id].get("library_tag", "user")
        data[material.id] = self._to_record(
            material,
            library_tag if library_tag is not None else existing_tag,
        )
        self._write(data)
        return material

    def delete(self, material_id: str) -> None:
        """Delete a material by ID. Raises KeyError if not found."""
        data = self._read()
        if material_id not in data:
            raise KeyError(f"Material '{material_id}' not found.")
        del data[material_id]
        self._write(data)

    def import_batch(
        self,
        records: list[dict],
        library_tag: str,
        overwrite: bool = False,
    ) -> tuple[list[str], list[str], list[str]]:
        """Bulk-import materials from a list of raw dicts.

        Args:
            records:     List of material dicts (id, name, density, composition).
            library_tag: Tag to assign all imported materials.
            overwrite:   If True, overwrite existing materials with same ID.
                         If False, skip them and add ID to skipped list.

        Returns:
            (imported_ids, skipped_ids, error_messages)
        """
        data = self._read()
        imported: list[str] = []
        skipped:  list[str] = []
        errors:   list[str] = []

        for i, item in enumerate(records):
            if not isinstance(item, dict):
                errors.append(f"Item {i}: not an object.")
                continue

            mat_id = item.get("id")
            if not mat_id:
                errors.append(f"Item {i}: missing 'id' field.")
                continue

            if mat_id in data and not overwrite:
                skipped.append(mat_id)
                continue

            try:
                record = {
                    "id":          mat_id,
                    "name":        item.get("name", mat_id),
                    "density":     float(item["density"]),
                    "library_tag": library_tag,
                    "composition": {
                        k: float(v)
                        for k, v in item.get("composition", {}).items()
                    },
                }
                data[mat_id] = record
                imported.append(mat_id)
            except (KeyError, ValueError, TypeError) as e:
                errors.append(f"Item {i} (id='{mat_id}'): {e}")

        if imported:
            self._write(data)

        return imported, skipped, errors

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _from_record(record: dict) -> Material:
        return Material(
            id=          record["id"],
            name=        record.get("name", record["id"]),
            density=     record.get("density"),
            composition= record.get("composition", {}),
        )

    @staticmethod
    def _to_record(material: Material, library_tag: str) -> dict:
        return {
            "id":          material.id,
            "name":        material.name,
            "density":     material.density,
            "library_tag": library_tag,
            "composition": material.composition,
        }