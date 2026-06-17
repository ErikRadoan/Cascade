"""Backend configuration models.

One Pydantic model per execution backend. These are the user-facing
configuration objects — what the frontend sends, what gets validated,
and what gets passed to the backend constructor.

The `type` discriminator field on each config lets Pydantic automatically
deserialize the correct subclass from a JSON payload like:

    {
        "type": "docker",
        "image": "cascade-openmc:latest",
        "memory_limit": "8g"
    }

Usage in the jobs API:
    config = BackendConfig.model_validate(raw_dict)   # auto-dispatches to subclass
    backend = BackendRegistry.create(config)           # instantiates the right backend

Adding a new backend:
    1. Add a new subclass of BackendConfig here
    2. Add it to the Annotated union in BackendConfig at the bottom
    3. Implement the corresponding ExecutionBackend subclass
    4. Register it in BackendRegistry.create()
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseBackendConfig(BaseModel):
    """Common fields shared by all backend configs."""

    # Nuclear data library — mounted read-only into every container/job
    # Points to a directory containing cross_sections.xml on the HOST machine.
    # For SLURM, this is a path on the cluster filesystem.
    nuclear_data_path: str = Field(
        default=str(Path.home() / ".cascade" / "data"),
        description=(
            "Host path to the nuclear data directory containing cross_sections.xml. "
            "Mounted read-only into every simulation environment."
        ),
    )

    # Jobs base directory — where per-job working directories are created
    jobs_base_dir: str = Field(
        default=str(Path.home() / ".cascade" / "jobs"),
        description="Host path where job working directories are created.",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Docker / Podman backend
# ---------------------------------------------------------------------------

class DockerBackendConfig(BaseBackendConfig):
    """Configuration for the Docker/Podman container backend.

    Runs OpenMC inside a container on the local machine.
    Suitable for development, single-machine research, and testing.

    Example frontend payload:
        backend_config:
          type: docker
          image: cascade-openmc:latest
          cli: podman
          memory_limit: 8g
          openmc_bin: /opt/miniconda/envs/openmc/bin/openmc
    """

    type: Literal["docker"] = "docker"

    # Container runtime
    cli: Literal["podman", "docker"] = Field(
        default="podman",
        description="Container CLI to use. 'podman' is recommended on Linux.",
    )

    # Container image
    image: str = Field(
        default="cascade-openmc:latest",
        description="Container image with OpenMC installed.",
    )

    # OpenMC binary path inside the container
    openmc_bin: str = Field(
        default="/opt/miniconda/envs/openmc/bin/openmc",
        description="Absolute path to the OpenMC binary inside the container.",
    )

    # Nuclear data path INSIDE the container (where the host path is mounted)
    nuclear_data_container_path: str = Field(
        default="/nuclear-data",
        description="Container path where nuclear_data_path is mounted.",
    )

    # Resource limits
    memory_limit: str = Field(
        default="4g",
        description="Container memory limit. e.g. '4g', '16g', '512m'.",
    )
    cpu_limit: str = Field(
        default="0",
        description=(
            "Container CPU limit. '0' = no limit. "
            "'2.0' = max 2 CPU cores."
        ),
    )

    @property
    def cross_sections_container_path(self) -> str:
        return f"{self.nuclear_data_container_path}/cross_sections.xml"


# ---------------------------------------------------------------------------
# Local backend (OpenMC installed directly, no container)
# ---------------------------------------------------------------------------

class LocalBackendConfig(BaseBackendConfig):
    """Configuration for the local process backend.

    Runs OpenMC as a direct subprocess — no container required.
    Requires OpenMC to be installed and on PATH (or at openmc_bin path).

    Useful when the user has OpenMC installed via conda/pip on their machine
    and doesn't want to run a container.

    Example frontend payload:
        backend_config:
          type: local
          openmc_bin: /home/user/miniconda/envs/openmc/bin/openmc
    """

    type: Literal["local"] = "local"

    openmc_bin: str = Field(
        default="openmc",
        description=(
            "Path to the OpenMC binary. "
            "Use 'openmc' if it's on PATH, or an absolute path otherwise."
        ),
    )

    nuclear_data_container_path: str = Field(
        default="",
        description="Not used for local backend — nuclear_data_path is used directly.",
    )


# ---------------------------------------------------------------------------
# SLURM backend (university HPC clusters)
# ---------------------------------------------------------------------------

class SlurmBackendConfig(BaseBackendConfig):
    """Configuration for the SLURM HPC cluster backend.

    Submits jobs via SSH to a SLURM login node using sbatch.
    Results are transferred back via SCP after completion.

    Most university clusters (including MetaCentrum/MUNI) run SLURM.
    OpenMC is typically available as an environment module — no container needed.

    Example frontend payload:
        backend_config:
          type: slurm
          host: metacentrum.muni.cz
          username: xradovan
          ssh_key_path: /home/user/.ssh/id_ed25519
          partition: gpu
          nodes: 1
          tasks_per_node: 8
          walltime: "04:00:00"
          openmc_module: OpenMC/0.15.3-foss-2023a
          remote_work_dir: /scratch/xradovan/cascade_jobs
          nuclear_data_path: /shared/openmc_data
    """

    type: Literal["slurm"] = "slurm"

    # SSH connection
    host: str = Field(
        ...,
        description="SLURM login node hostname or IP address.",
    )
    username: str = Field(
        ...,
        description="SSH username on the cluster.",
    )
    ssh_key_path: str = Field(
        default=str(Path.home() / ".ssh" / "id_ed25519"),
        description="Path to the SSH private key on the LOCAL machine.",
    )
    ssh_port: int = Field(
        default=22,
        description="SSH port (default 22).",
    )

    # SLURM job parameters
    partition: str = Field(
        default="compute",
        description="SLURM partition/queue name. Cluster-specific.",
    )
    nodes: int = Field(
        default=1, gt=0,
        description="Number of nodes to request.",
    )
    tasks_per_node: int = Field(
        default=8, gt=0,
        description="MPI tasks per node (= CPU cores for OpenMC).",
    )
    walltime: str = Field(
        default="02:00:00",
        description="Maximum wall time in HH:MM:SS format.",
    )
    memory_per_node: str = Field(
        default="16G",
        description="Memory per node. e.g. '16G', '64G'.",
    )
    account: str | None = Field(
        default=None,
        description="SLURM account/project to charge. Leave None if not required.",
    )

    # OpenMC on the cluster
    openmc_module: str | None = Field(
        default=None,
        description=(
            "Environment module to load before running OpenMC. "
            "e.g. 'OpenMC/0.15.3-foss-2023a'. "
            "If None, openmc_bin is used directly."
        ),
    )
    openmc_bin: str = Field(
        default="openmc",
        description=(
            "OpenMC binary path on the CLUSTER (not local machine). "
            "Only used when openmc_module is None."
        ),
    )

    # Remote filesystem
    remote_work_dir: str = Field(
        ...,
        description=(
            "Directory on the CLUSTER where job files are staged. "
            "Must be writable and accessible from compute nodes. "
            "e.g. '/scratch/username/cascade_jobs'"
        ),
    )


# ---------------------------------------------------------------------------
# Discriminated union — Pydantic auto-dispatches on the `type` field
# ---------------------------------------------------------------------------

BackendConfig = Annotated[
    DockerBackendConfig | LocalBackendConfig | SlurmBackendConfig,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# Backend registry — instantiates the right ExecutionBackend from a config
# ---------------------------------------------------------------------------

def create_backend(config: DockerBackendConfig | LocalBackendConfig | SlurmBackendConfig):
    """Instantiate the correct ExecutionBackend for the given config.

    Args:
        config: A validated backend config instance.

    Returns:
        An ExecutionBackend ready to call submit() on.

    Raises:
        NotImplementedError: If the backend type isn't implemented yet.
        ValueError: If the config type is unrecognised.
    """
    if isinstance(config, DockerBackendConfig):
        from .docker_backend import DockerBackend
        return DockerBackend.from_config(config)

    if isinstance(config, LocalBackendConfig):
        from .local import LocalBackend
        return LocalBackend.from_config(config)

    if isinstance(config, SlurmBackendConfig):
        from .slurm import SlurmBackend
        return SlurmBackend.from_config(config)

    raise ValueError(f"Unrecognised backend config type: {type(config).__name__}")