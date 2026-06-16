# cascade/config.py  (you have this file already as a stub)
from pydantic_settings import BaseSettings
from pathlib import Path

class CascadeSettings(BaseSettings):
    # Base directory for all Cascade user data
    cascade_home: Path = Path.home() / ".cascade"

    # Derived paths — computed from cascade_home
    @property
    def data_dir(self) -> Path:
        return self.cascade_home / "data"

    @property
    def jobs_dir(self) -> Path:
        return self.cascade_home / "jobs"

    @property
    def cross_sections_path(self) -> Path:
        return self.data_dir / "cross_sections.xml"

    @property
    def materials_library_path(self) -> Path:
        return self.data_dir / "materials" / "materials.json"

    # Container configuration
    container_cli:   str  = "podman"
    container_image: str  = "cascade-openmc:latest"
    openmc_bin:      str  = "/opt/miniconda/envs/openmc/bin/openmc"

    # Container mount paths (inside the container — fixed, never change)
    container_data_mount: str = "/data"
    container_work_mount: str = "/work"

    class Config:
        env_prefix = "CASCADE_"   # override any setting with CASCADE_* env vars