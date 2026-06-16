"""Database configuration scaffold."""
from __future__ import annotations
from dataclasses import dataclass
import os
@dataclass(slots=True)
class DatabaseConfig:
    url: str = os.getenv("CASCADE_DATABASE_URL", "sqlite:///./cascade.db")
def get_database_config() -> DatabaseConfig:
    return DatabaseConfig()

