"""Material domain model."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex_color(value: str | None) -> str | None:
    """Normalise a user-supplied colour to canonical ``#RRGGBB`` (uppercase).

    ``None`` / empty / whitespace-only -> ``None`` (meaning "no override: use
    the automatic palette colour"). Accepts ``#RGB``, ``#RRGGBB``, with or
    without the leading ``#``. Anything else raises ``ValueError`` — callers
    (API validation, JSON import) turn that into a 422 / per-item error.
    """
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    m = _HEX_RE.match(v)
    if m is None:
        raise ValueError(f"Invalid colour {value!r}: expected hex like '#E8703A'.")
    digits = m.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits.upper()


@dataclass(slots=True)
class Material:
    id: str
    name: str
    density: float | None = None
    composition: dict[str, float] = field(default_factory=dict)
    # Optional user-chosen display colour (#RRGGBB). Purely cosmetic — used by
    # the 3D viewports, never exported to OpenMC. None = automatic palette.
    color: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "density": self.density,
            "composition": dict(self.composition),
            "color": self.color,
        }
